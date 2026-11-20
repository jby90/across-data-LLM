# ruff: noqa: E501
"""Minimalist weekly automotive data-compliance digest: HTML/EML builder, SMTP push, and portal sync.

Design principles (aligned with 11.04 meeting & matrix rows 22, 23, 25):
- Strictly separated from the statutory knowledge library (opinion items are never indexed as law).
- Minimalist objective digest: title, publisher, country/region tag, publish date, factual excerpt/reason,
  and original source URL — zero subjective speculation.
- Supports scheduled weekly execution + manual one-click preview & send, SMTP delivery when configured,
  local .eml/.html archive for private deployments, and direct user-portal reading with country-briefing jump.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import smtplib
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.opinion.models import OpinionDigestConfig, OpinionDigestRun, OpinionItem

log = logging.getLogger(__name__)

LEVEL_LABEL = {"high": "高关注", "medium": "中关注", "low": "常规动态"}
LEVEL_ALLOWED = {
    "high": ("high",),
    "medium": ("high", "medium"),
    "low": ("high", "medium", "low"),
}
_ORDER = case((OpinionItem.level == "high", 0), (OpinionItem.level == "medium", 1), else_=2)

_COUNTRY_HINTS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("SG", "新加坡", re.compile(r"新加坡|PDPC|LTA|IMDA|Singapore", re.I)),
    ("EG", "埃及", re.compile(r"埃及|NTRA|Egypt", re.I)),
    ("MA", "摩洛哥", re.compile(r"摩洛哥|CNDP|ANRT|Morocco", re.I)),
    ("TN", "突尼斯", re.compile(r"突尼斯|INPDP|Tunisia", re.I)),
    ("LB", "黎巴嫩", re.compile(r"黎巴嫩|Lebanon", re.I)),
    ("DE", "德国", re.compile(r"德国|KBA|BfDI|Germany", re.I)),
    ("FR", "法国", re.compile(r"法国|CNIL|France", re.I)),
    ("EU", "欧盟", re.compile(r"欧盟|GDPR|EDPB|欧洲数据保护", re.I)),
    ("GB", "英国", re.compile(r"英国|ICO|United Kingdom", re.I)),
    ("US", "美国", re.compile(r"美国|NHTSA|FTC|加州|United States", re.I)),
    ("JP", "日本", re.compile(r"日本|PPC|MLIT|Japan", re.I)),
    ("KR", "韩国", re.compile(r"韩国|PIPC|Korea", re.I)),
    ("TH", "泰国", re.compile(r"泰国|PDPC Thailand|Thailand", re.I)),
    ("MY", "马来西亚", re.compile(r"马来西亚|Malaysia", re.I)),
    ("ID", "印度尼西亚", re.compile(r"印尼|印度尼西亚|Indonesia", re.I)),
    ("AE", "阿联酋", re.compile(r"阿联酋|迪拜|UAE", re.I)),
    ("SA", "沙特阿拉伯", re.compile(r"沙特|SDAIA|Saudi", re.I)),
    ("BR", "巴西", re.compile(r"巴西|ANPD|Brazil", re.I)),
    ("CN", "中国", re.compile(r"网信办|工信部|工业和信息化部|市场监管总局|自然资源部|交通运输部|标准委|中汽协|中国汽车|新华社|人民网|北京|上海|深圳|海南|粤港澳")),
)

_PORTAL_CHROME_RE = re.compile(
    r"(?:\[?\s*邮箱登录\s*\]?|旧版网站|设为首页|加入收藏|协会概况\s+协会工作|\[\s*字号[：:]|字体[：:]\s*小\s*中\s*大|分享到[：:]|新华网\s*>\s*>\s*正文|当前位置[：:]|您现在的位置[：:]|【\s*(?:打印|纠错|关闭|收藏|大|中|小)\s*】|\[\s*(?:打印|纠错|关闭)\s*\])"
)
_INLINE_NOISE_RE = re.compile(
    r"(?:【\s*(?:打印|纠错|关闭|收藏|字体[：:][^】]*)\s*】"
    r"|\[\s*(?:打印|纠错|关闭|字体[：:][^\]]*)\s*\]"
    r"|(?:\d{4}[年\-/]\d{1,2}[月\-/]\d{1,2}日?\s*(?:\d{1,2}:\d{2}(?::\d{2})?)?\s*)?来源\s*[：:]\s*[^\s【】\[\]。；]{2,24}"
    r"|责任编辑\s*[：:]\s*[^\s。；]{1,16}"
    r"|(?:最新新闻|相关新闻|相关阅读|延伸阅读)\s*[·:：].*$"
    r"|附件\s*\d*\s*[：:].*$)"
)
_FOOTER_CUTOFF_RE = re.compile(
    r"^(?:最新新闻|相关新闻|相关阅读|延伸阅读|推荐阅读|热点推荐|版权声明|免责声明)\b"
)
_BREADCRUMB_PREFIX_RE = re.compile(
    r"^(?:(?:您现在的位置|当前位置|所在位置)\s*[：:]\s*)?"
    r"(?:首页|首\s*页|Home|新华网|人民网|中国网信网|中国汽车工业协会|工业和信息化部)"
    r"(?:\s*(?:>\s*>|>>|>|»|›|＞|→|/)\s*[^\s。！？；>»›＞]{1,24})+\s*(?:正文\s*)?",
    re.I,
)
_BREADCRUMB_LINE_RE = re.compile(
    r"^(?:(?:您现在的位置|当前位置|所在位置)\s*[：:]\s*)?"
    r"[^\n。！？]{1,20}(?:\s*(?:>\s*>|>>|>|»|›|＞|→)\s*[^\n。！？]{1,24}){1,6}\s*$"
)


def strip_portal_noise(text: str) -> str:
    if not text:
        return ""
    out: list[str] = []
    for raw in text.splitlines():
        ln = re.sub(r"\s+", " ", raw).strip()
        if not ln:
            continue
        if _FOOTER_CUTOFF_RE.match(ln):
            break
        ln = _BREADCRUMB_PREFIX_RE.sub("", ln).strip()
        ln = _INLINE_NOISE_RE.sub("", ln).strip()
        ln = re.sub(r"^\d{4}[年\-/]\d{1,2}[月\-/]\d{1,2}日?\s*(?:\d{1,2}:\d{2}(?::\d{2})?)?\s*", "", ln).strip()
        if not ln or _BREADCRUMB_LINE_RE.match(ln):
            continue
        if _PORTAL_CHROME_RE.search(ln):
            ln = re.sub(
                r"^.*?(?:\[\s*打印\s*\]|【\s*纠错\s*】|分享到[：:]\S*|来源[：:]\s*\S+\s*|正文\s*)", "", ln
            ).strip()
            ln = _BREADCRUMB_PREFIX_RE.sub("", ln).strip()
            ln = _INLINE_NOISE_RE.sub("", ln).strip()
            if len(ln) < 16 or _PORTAL_CHROME_RE.search(ln) or _BREADCRUMB_LINE_RE.match(ln):
                continue
        if re.fullmatch(r"(?:首页|首\s*页|时政要闻|网信政务|互动服务|热点专题|English|\s|\|)+", ln):
            continue
        out.append(ln)
    return "\n".join(out).strip()


_CN_PUBLISHER_RE = re.compile(
    r"中汽协|中国汽车|网信办|国家互联网信息办公室|工信部|工业和信息化部|市场监管总局|自然资源部|交通运输部|标准委|新华社|人民网"
)


def infer_country_tag(title: str, publisher: str, body: str) -> tuple[str, str]:
    clean_body = strip_portal_noise(body or "")
    head = f"{title} {publisher}"
    # Domestic regulatory / industry association notices should be tagged China unless title names a single foreign state
    if _CN_PUBLISHER_RE.search(publisher or "") or re.match(r"^\s*(?:中汽协|中国|工信部|网信办|国家)", title or ""):
        foreign_in_title = [
            (code, name_zh)
            for code, name_zh, pat in _COUNTRY_HINTS
            if code != "CN" and pat.search(title or "")
        ]
        if not foreign_in_title:
            return "CN", "中国"
    for code, name_zh, pat in _COUNTRY_HINTS:
        if pat.search(head):
            return code, name_zh
    blob = f"{head} {clean_body[:240]}"
    for code, name_zh, pat in _COUNTRY_HINTS:
        if pat.search(blob):
            return code, name_zh
    if re.search(r"欧洲|东盟|北非", blob, re.I):
        return "", "多国与区域"
    if re.search(r"北美", blob, re.I):
        return "", "北美地区"
    return "", "国际与行业"


def finalize_digest_summary(text: str) -> str:
    """Normalize digest summary punctuation so excerpts never end abruptly on a semicolon or comma."""
    s = strip_portal_noise(text or "").strip()
    if not s:
        return ""
    s = re.sub(r"[；;，,、：:]+\s*$", "。", s)
    if s and s[-1] not in ("。", "！", "？", "…", ".", "!", "?", "”", "）", ")"):
        s += "。"
    return s


def clean_objective_summary(row: OpinionItem) -> str:
    cleaned = strip_portal_noise(row.body or "")
    body = re.sub(r"\s+", " ", cleaned)
    if body:
        parts = [p for p in re.split(r"(?<=[。！？；])", body) if p.strip()]
        picked = "".join(parts[:2]).strip()
        if len(picked) > 220:
            first_only = parts[0].strip() if parts else ""
            if 20 <= len(first_only) <= 220:
                picked = first_only
            else:
                cut_zone = picked[:215]
                last_sep = max(cut_zone.rfind("；"), cut_zone.rfind("，"), cut_zone.rfind("。"))
                picked = (cut_zone[:last_sep] if last_sep >= 40 else cut_zone).rstrip("，、；;：: ") + "。"
        if picked:
            return finalize_digest_summary(picked)
    reasons = [str(x).strip() for x in (row.bases or []) if str(x).strip()]
    return finalize_digest_summary("；".join(reasons[:2])) if reasons else row.title


def compute_next_digest_run(now: datetime, weekday: int = 1, hour_utc: int = 1) -> datetime:
    """Next occurrence of ISO weekday (1=Mon..7=Sun) at hour_utc:00 UTC."""
    wd = max(1, min(7, int(weekday or 1)))
    hr = max(0, min(23, int(hour_utc or 0)))
    candidate = now.astimezone(UTC).replace(hour=hr, minute=0, second=0, microsecond=0)
    days_ahead = (wd - candidate.isoweekday()) % 7
    candidate = candidate + timedelta(days=days_ahead)
    if candidate <= now.astimezone(UTC):
        candidate = candidate + timedelta(days=7)
    return candidate


async def get_or_create_config(session: AsyncSession) -> OpinionDigestConfig:
    from sqlalchemy import text as sa_text

    now = datetime.now(UTC)
    await session.execute(sa_text("SELECT pg_advisory_xact_lock(64042012)"))
    cfg = await session.scalar(
        select(OpinionDigestConfig)
        .order_by(OpinionDigestConfig.updated_at.asc(), OpinionDigestConfig.id.asc())
        .limit(1)
    )
    if cfg is None:
        cfg = OpinionDigestConfig(
            enabled=True,
            weekday=1,
            hour_utc=1,
            lookback_days=7,
            min_level="low",
            recipients=[],
            smtp_host=os.environ.get("CYSH_SMTP_HOST", ""),
            smtp_port=int(os.environ.get("CYSH_SMTP_PORT", "587") or 587),
            smtp_user=os.environ.get("CYSH_SMTP_USER", ""),
            smtp_from=os.environ.get("CYSH_SMTP_FROM", ""),
            next_run_at=compute_next_digest_run(now, 1, 1),
        )
        session.add(cfg)
        await session.flush()
    elif cfg.enabled and (cfg.next_run_at is None or cfg.next_run_at <= now):
        cfg.next_run_at = compute_next_digest_run(now, cfg.weekday, cfg.hour_utc)
        await session.flush()
    return cfg


async def build_digest_payload(
    session: AsyncSession,
    *,
    lookback_days: int = 7,
    min_level: str = "low",
    max_items: int = 15,
) -> dict:
    today = date.today()
    days = max(1, min(90, int(lookback_days or 7)))
    period_start = today - timedelta(days=days)
    allowed_levels = LEVEL_ALLOWED.get(min_level, LEVEL_ALLOWED["low"])

    stmt = (
        select(OpinionItem)
        .where(OpinionItem.level.in_(allowed_levels), OpinionItem.published_on >= period_start)
        .order_by(OpinionItem.published_on.desc().nulls_last(), OpinionItem.fetched_at.desc())
        .limit(max_items)
    )
    rows = list((await session.scalars(stmt)).all())
    fallback_recent = False
    period_end = today
    if not rows:
        # 严格限制近期回退下限（优先近 30 日，再回退本年度内最新收录动态），绝不跨年回退陈旧历史新闻
        for floor in (today - timedelta(days=max(days, 30)), date(today.year, 1, 1)):
            stmt_recent = (
                select(OpinionItem)
                .where(OpinionItem.level.in_(allowed_levels), OpinionItem.published_on >= floor)
                .order_by(OpinionItem.published_on.desc().nulls_last(), OpinionItem.fetched_at.desc())
                .limit(min(10, max_items))
            )
            rows = list((await session.scalars(stmt_recent)).all())
            if rows:
                fallback_recent = True
                dated = [r.published_on for r in rows if r.published_on]
                if dated:
                    period_start = min(dated)
                    period_end = max(dated)
                break

    digest_items: list[dict] = []
    for r in rows:
        cc, country_label = infer_country_tag(r.title, r.publisher, r.body or "")
        digest_items.append(
            {
                "id": str(r.id),
                "title": r.title,
                "publisher": r.publisher,
                "country_code": cc,
                "country_label": country_label,
                "published_on": r.published_on.isoformat() if r.published_on else "近期收录",
                "level": r.level or "low",
                "level_label": LEVEL_LABEL.get(r.level or "low", "常规动态"),
                "summary": clean_objective_summary(r),
                "url": r.url,
            }
        )

    if fallback_recent or (period_end - period_start).days > 21:
        period_label = f"近期归档动态精选（共 {len(digest_items)} 条）"
    else:
        period_label = f"{period_start.isoformat()} 至 {period_end.isoformat()}"
    subject = f"车研数合 · 汽车数据跨境与安全合规周报（{today.isoformat()}）"
    html_body = render_digest_html(subject, period_label, digest_items, fallback_recent=fallback_recent)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "period_label": period_label,
        "subject": subject,
        "item_count": len(digest_items),
        "items": digest_items,
        "html_body": html_body,
    }


def render_digest_html(
    subject: str, period_label: str, items: list[dict], *, fallback_recent: bool = False
) -> str:
    rows_html: list[str] = []
    for idx, it in enumerate(items, 1):
        title_esc = html.escape(str(it.get("title") or ""))
        url_esc = html.escape(str(it.get("url") or ""))
        pub_esc = html.escape(str(it.get("publisher") or ""))
        country_esc = html.escape(str(it.get("country_label") or ""))
        date_esc = html.escape(str(it.get("published_on") or ""))
        rows_html.append(
            f'<div style="padding:12px 14px;border-bottom:1px solid #e2e8f0;background:#ffffff;">'
            f'<div style="font-size:15px;font-weight:600;line-height:1.6;margin-bottom:4px;">'
            f'<span style="color:#64748b;margin-right:6px;">{idx}.</span>'
            f'<a href="{url_esc}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#0f172a;text-decoration:none;">{title_esc}</a>'
            f"</div>"
            f'<div style="font-size:13px;color:#64748b;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;">'
            f"<span>{country_esc} · {pub_esc} · {date_esc}</span>"
            f'<a href="{url_esc}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#2563eb;text-decoration:none;">阅读官方原文 ↗</a>'
            f"</div>"
            f"</div>"
        )
    body_html = (
        f'<div style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">{"".join(rows_html)}</div>'
        if rows_html
        else '<p style="color:#64748b;font-size:14px;">本统计周期暂无新收录资讯。</p>'
    )
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:20px;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,\'Microsoft YaHei\',sans-serif;">'
        '<div style="max-width:720px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:22px 26px;">'
        f'<h1 style="margin:0 0 8px;font-size:19px;font-weight:600;color:#0f172a;">{html.escape(subject)}</h1>'
        f'<p style="margin:0 0 16px;font-size:13px;color:#64748b;">统计范围：{html.escape(period_label)}'
        f'{" · 共 " + str(len(items)) + " 条官方与行业动态" if "共 " not in period_label else ""}'
        f"（独立于法规知识库，仅客观汇集标题与官方来源链接）</p>"
        f"{body_html}"
        '<p style="margin:16px 0 0;padding-top:12px;border-top:1px solid #e2e8f0;font-size:13px;color:#64748b;">'
        "车研数合汽车数据跨境合规平台自动汇编 · 点击标题或链接直达官方发布机构原文</p>"
        "</div></body></html>"
    )


def _send_smtp_sync(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    sender: str,
    recipients: list[str],
    subject: str,
    html_body: str,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender or user or "noreply@cyshuhe.local"
    msg["To"] = ", ".join(recipients)
    msg.set_content("请使用支持 HTML 的邮件客户端查阅本期合规周报。")
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        if port in (587, 25):
            try:
                smtp.starttls()
            except smtplib.SMTPException:
                pass
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)


def _sanitize_header_val(val: str) -> str:
    return re.sub(r"[\r\n\x00-\x1f\x7f]+", "", val or "").strip()


def _archive_local_eml(run_id: str, subject: str, recipients: list[str], html_body: str) -> str:
    from app.core.config import get_settings

    out_dir = Path(get_settings().archive_dir) / "opinion_digests"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"digest_{run_id}.html"
    html_path.write_text(html_body, encoding="utf-8")
    msg = EmailMessage()
    msg["Subject"] = _sanitize_header_val(subject)
    msg["From"] = "digest@cyshuhe.local"
    clean_rcpts = [_sanitize_header_val(r) for r in recipients if _sanitize_header_val(r)]
    msg["To"] = ", ".join(clean_rcpts) if clean_rcpts else "portal-subscribers@cyshuhe.local"
    msg.set_content("请使用支持 HTML 的邮件客户端查阅本期合规周报。")
    msg.add_alternative(html_body, subtype="html")
    eml_path = out_dir / f"digest_{run_id}.eml"
    eml_path.write_bytes(bytes(msg))
    return str(eml_path)


async def execute_digest_send(
    session: AsyncSession,
    *,
    override_recipients: list[str] | None = None,
) -> OpinionDigestRun:
    cfg = await get_or_create_config(session)
    payload = await build_digest_payload(
        session,
        lookback_days=cfg.lookback_days,
        min_level=cfg.min_level,
    )
    rcpts = [
        r.strip()
        for r in (override_recipients if override_recipients is not None else (cfg.recipients or []))
        if isinstance(r, str) and "@" in r.strip()
    ]
    host = (cfg.smtp_host or os.environ.get("CYSH_SMTP_HOST", "")).strip()
    port = int(cfg.smtp_port or os.environ.get("CYSH_SMTP_PORT", "587") or 587)
    user = (cfg.smtp_user or os.environ.get("CYSH_SMTP_USER", "")).strip()
    sender = (cfg.smtp_from or os.environ.get("CYSH_SMTP_FROM", "") or user).strip()
    password = os.environ.get("CYSH_SMTP_PASSWORD", "")

    run = OpinionDigestRun(
        period_start=payload["period_start"],
        period_end=payload["period_end"],
        subject=payload["subject"],
        item_count=payload["item_count"],
        items_json=payload["items"],
        html_body=payload["html_body"],
        recipients=rcpts,
    )
    session.add(run)
    await session.flush()

    if host and rcpts:
        try:
            await asyncio.to_thread(
                _send_smtp_sync,
                host=host,
                port=port,
                user=user,
                password=password,
                sender=sender,
                recipients=rcpts,
                subject=payload["subject"],
                html_body=payload["html_body"],
            )
            run.delivery_mode = "smtp"
            run.status = "sent"
            run.note = f"已发送至 {len(rcpts)} 个订阅邮箱，并同步发布至用户端行业合规周报专栏"
        except Exception:  # noqa: BLE001
            await asyncio.to_thread(
                _archive_local_eml, str(run.id), payload["subject"], rcpts, payload["html_body"]
            )
            run.delivery_mode = "local_archive"
            run.status = "archived"
            run.note = "邮件服务器暂未连通，已完成平台归档并同步发布至用户端行业合规周报专栏"
    else:
        await asyncio.to_thread(
            _archive_local_eml, str(run.id), payload["subject"], rcpts, payload["html_body"]
        )
        run.delivery_mode = "local_archive"
        run.status = "sent"
        run.note = (
            "已生成本期周报并同步发布至用户端行业合规周报专栏"
            + (
                f"（已登记 {len(rcpts)} 个订阅邮箱，配置发信邮件服务器后自动外发）"
                if rcpts
                else "（可在推送设置中填写订阅邮箱）"
            )
        )

    cfg.next_run_at = compute_next_digest_run(datetime.now(UTC), cfg.weekday, cfg.hour_utc)
    await session.flush()
    return run


async def run_due_digest_if_needed(sf: async_sessionmaker[AsyncSession], now: datetime | None = None) -> bool:
    """Called by background scheduler: if weekly digest is enabled and next_run_at <= now, generate & send."""
    from app.core.rls import system_scope

    cur = now or datetime.now(UTC)
    try:
        with system_scope("opinion-digest"):
            async with sf() as s:
                cfg = await s.scalar(
                    select(OpinionDigestConfig)
                    .where(
                        OpinionDigestConfig.enabled.is_(True),
                        OpinionDigestConfig.next_run_at.is_not(None),
                        OpinionDigestConfig.next_run_at <= cur,
                    )
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if cfg is None:
                    return False
                await execute_digest_send(s)
                await s.commit()
                return True
    except Exception:  # noqa: BLE001
        log.exception("舆情合规周报定时任务执行异常")
        return False

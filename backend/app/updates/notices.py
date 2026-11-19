# ruff: noqa: E501
"""Update notices the enterprise gets when the administrator changes what its projects rely on.

Two kinds besides “a cited law changed” (service.py):
- a country's briefing has a new edition: only enterprises with a compliance project or an admission
  project for that country, and only for an edition written after that project was started;
- an admission item a project is preparing was changed (steps, materials, summary, or taken off the
  list): compared with the state the project last saw, whatever made the change (the admin page or a
  rebuild command).

Notices are worked out when the enterprise's notice list or bell is read, one per user per event
(``dedupe_key``), so no change can slip past by being made outside the admin page.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission.models import AdmissionEntry, AdmissionProject, AdmissionStep
from app.analysis.models import AnalysisTask
from app.analysis.workflow_engine import COUNTRY_NAMES_ZH
from app.iam.models import User
from app.updates.models import UserNotification


def _name(code: str) -> str:
    from app.analysis.document_basis import NAMES

    return NAMES.get(code, COUNTRY_NAMES_ZH.get(code, code))


def _local_date(value: datetime | None) -> str:
    if value is None:
        return ""
    from datetime import timedelta, timezone

    v = value.astimezone(timezone(timedelta(hours=8)))  # 北京时间（本机不一定装了时区库）
    return f"{v.year} 年 {v.month} 月 {v.day} 日"


async def entry_hash(session: AsyncSession, entry: AdmissionEntry) -> str:
    """What the user sees of an admission item: its name, authority, summary, whether it is still
    listed, its card fields and its steps with materials."""
    steps = (
        await session.scalars(select(AdmissionStep).where(AdmissionStep.entry_id == entry.id).order_by(AdmissionStep.seq))
    ).all()
    body = {
        "name": entry.name_zh,
        "authority": entry.authority_zh,
        "summary": entry.summary_zh,
        "listed": entry.review_status != "out_of_scope" and entry.merged_into is None,
        "profile": entry.profile or {},
        "steps": [[s.seq, s.title_zh, s.detail_zh, s.materials or []] for s in steps],
    }
    if "materials_plan" in (entry.edited or []):
        # 管理员改了递交清单（材料怎么准备）也算事项有变化；没改过的不计入，免得给所有项目补发提醒
        body["bring"] = entry.materials_plan
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _norm(text: str) -> str:
    return re.sub(r"[\s，,。.、；;：:（）()“”\"'·]", "", text or "")


async def entry_snapshot(session: AsyncSession, entry: AdmissionEntry) -> dict:
    """What an enterprise preparing this item relies on: its name, receiving body, whether it is
    still listed, the steps and their materials, and the originals to bring. Wording-only edits
    (spaces, punctuation) compare equal; the card's descriptive fields (eligibility wording...)
    are not part of it, so a rebuild that only rewords them sends no notice."""
    from app.admission.material_kinds import plan_of

    steps = (
        await session.scalars(select(AdmissionStep).where(AdmissionStep.entry_id == entry.id).order_by(AdmissionStep.seq))
    ).all()
    plan = plan_of(entry, [m for st in steps for m in st.materials or []])
    from app.admission.router import display_authority

    return {
        "name": entry.name_zh or "",
        "authority": display_authority(entry.authority_zh or "", entry.country_code),
        "listed": entry.review_status != "out_of_scope" and entry.merged_into is None,
        "steps": [st.title_zh for st in steps],
        # 写进申请书的信息（“数据保护负责人指定信息”）不是另一份文件：并进申请书或改成填写项不算去掉
        "materials": list(dict.fromkeys(m for st in steps for m in st.materials or [] if not re.search(r"信息$", m.strip()))),
        # 递交时自带的原件：只算法规环节里列的；按资格补的证明原件是平台整理出来的，不算事项变化
        "bring": [x["name"] for x in plan if x.get("kind") == "original" and not x.get("proves")],
    }


def _base(text: str) -> str:
    """A material's own name: its notes in brackets are wording, not a different document."""
    return _norm(re.sub(r"[（(][^）)]*[）)]", "", text or ""))


def _diff_list(label: str, old: list[str], new: list[str]) -> str:
    o, n = {_base(x): x for x in old}, {_base(x): x for x in new}
    added = [n[k] for k in n if k not in o]
    gone = [o[k] for k in o if k not in n]
    parts = ([f"增补「{'」「'.join(added[:4])}」" + (f"等 {len(added)} 项" if len(added) > 4 else "")] if added else []) + (
        [f"核减「{'」「'.join(gone[:4])}」" + (f"等 {len(gone)} 项" if len(gone) > 4 else "")] if gone else []
    )
    if not parts and [_base(x) for x in old] != [_base(x) for x in new] and sorted(o) != sorted(n):
        parts = ["申报顺序调整"]
    return f"{label}（{'；'.join(parts)}）" if parts else ""


def entry_changes(old: dict, new: dict) -> list[str]:
    """Each changed part, with before and after (“受理机构：A → B”)."""
    out = []
    # 补写或修正资料（未写明 → 写明、只是写法不同）不算变化；两边都写明了、又不是同一个机构才算
    a, b = old.get("authority", ""), new.get("authority", "")
    unnamed = re.compile(r"^$|未写明|主管机关|主管机构|主管部门|专门服务|^[^一-鿿]*$")  # 泛称换成具体机构是补写，不是换了机构
    abbr = lambda t: set(re.findall(r"[A-Z]{2,}", t or ""))  # noqa: E731  同一缩写（如 ANRT）是同一机构，只是写法不同
    if not unnamed.search(a) and not unnamed.search(b) and _base(a) != _base(b) and _base(a) not in _base(b) and _base(b) not in _base(a) and not (abbr(a) & abbr(b)):
        out.append(f"法定受理机构调整（由「{a}」更新为「{b}」）")
    for key, label in (("steps", "法定办理环节"), ("materials", "申报要件清单"), ("bring", "递交核验原件")):
        info = (lambda xs: [x for x in xs if not re.search(r"信息$", x.strip())]) if key == "materials" else (lambda xs: xs)  # noqa: E731  旧快照里的“…信息”同样不算文件
        line = _diff_list(label, info(old.get(key) or []), info(new.get(key) or []))
        if line:
            out.append(line)
    return out


def _first_text(blocks) -> str:
    for b in blocks:
        t = str(b.get("text") or "").strip() if isinstance(b, dict) else ""
        if t and b.get("type") in ("paragraph", "list", None):
            return t
        for it in (b.get("items") or []) if isinstance(b, dict) else []:
            if str(it).strip():
                return str(it).strip()
    return ""


def _short(t: str, n: int = 70) -> str:
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[:n].rstrip("，。；、 ") + "……"


def _diff_around(old_text: str, new_text: str, radius: int = 30) -> tuple[str, str]:
    """Extract concise snippets around the first differing character offset ``diff_idx``."""
    a = " ".join((old_text or "").split())
    b = " ".join((new_text or "").split())
    if not a:
        return "", _short(b, radius * 2)
    limit = min(len(a), len(b))
    diff_idx = limit
    for idx in range(limit):
        if a[idx] != b[idx]:
            diff_idx = idx
            break

    def _slice(s: str) -> str:
        if not s:
            return ""
        window = radius * 2
        if len(s) <= window:
            return s
        start = max(0, min(diff_idx, len(s)) - radius)
        if start > 0:
            left_sep = max(s.rfind(ch, max(0, start - 16), start + 1) for ch in ("，", "。", "；", "：", "、", " "))
            if left_sep >= 0:
                start = left_sep + 1
            else:
                right_sep = min([i for i in (s.find(ch, start, min(len(s), diff_idx)) for ch in ("，", "。", "；", "：", "、")) if i >= 0] or [-1])
                if right_sep >= 0:
                    start = right_sep + 1
        end = min(len(s), start + window)
        if end < len(s):
            right_cut = max(s.rfind(ch, max(diff_idx + 4, end - 14), min(len(s), end + 12)) for ch in ("，", "。", "；", "、", "）", ")"))
            if right_cut > start + 12:
                end = right_cut + (1 if s[right_cut] in ("）", ")") else 0)
        seg = s[start:end].strip("，。；、 ")
        return ("…" if start > 0 else "") + seg + ("…" if end < len(s) else "")

    return _slice(a), _slice(b)


async def _changed_conclusions(session: AsyncSession, latest) -> list[tuple[str, str, str]]:
    """(section, conclusion before, conclusion now) for the sections whose first paragraph changed."""
    from app.knowledge.whitepaper import CountryWhitepaper

    prev = await session.scalar(
        select(CountryWhitepaper).where(CountryWhitepaper.country_code == latest.country_code, CountryWhitepaper.version_no < latest.version_no)
        .order_by(CountryWhitepaper.version_no.desc()).limit(1)
    )
    if prev is None:
        return []

    def sections(blocks) -> dict[str, list]:
        out, head = {}, ""
        for b in blocks or []:
            if b.get("type") == "heading" and b.get("level", 1) <= 2:
                head = str(b.get("text") or "")
                out[head] = []
                continue
            if head:
                out[head].append(b)
        return out

    old, new = sections(prev.blocks), sections(latest.blocks)
    out = []
    for h, blocks in new.items():
        if not re.match(r"^[一二三四五六七八九十]+、", h):
            continue
        a, b = _first_text(old.get(h, [])), _first_text(blocks)
        if a != b and b:
            old_snip, new_snip = _diff_around(a, b, radius=30)
            out.append((re.sub(r"^[一二三四五六七八九十\d]+[、.．]\s*", "", h), old_snip, new_snip))
    return out[:3]


async def _changed_parts(session: AsyncSession, latest) -> list[str]:
    """The briefing's parts (its headings) whose text differs from the previous edition."""
    from app.knowledge.whitepaper import CountryWhitepaper

    prev = await session.scalar(
        select(CountryWhitepaper).where(CountryWhitepaper.country_code == latest.country_code, CountryWhitepaper.version_no < latest.version_no)
        .order_by(CountryWhitepaper.version_no.desc()).limit(1)
    )
    if prev is None:
        return []

    def parts(blocks) -> dict[str, str]:
        out, head = {}, ""
        for b in blocks or []:
            if b.get("type") == "heading" and b.get("level", 1) <= 2:
                head = str(b.get("text") or "")
                continue
            if head:
                out[head] = out.get(head, "") + json.dumps(b, ensure_ascii=False, sort_keys=True)
        return out

    old, new = parts(prev.blocks), parts(latest.blocks)
    names = [h for h in new if new[h] != old.get(h) and re.match(r"^[一二三四五六七八九十]+、", h)]
    return [re.sub(r"^[一二三四五六七八九十\d]+[、.．]\s*", "", h) for h in names][:4]


async def _send(session: AsyncSession, org_id: uuid.UUID, users: list[uuid.UUID], *, kind: str, key: str, title: str, body: str, link: str) -> None:
    from sqlalchemy import delete

    # 同一件事又有更新（同一国简报再出一版、同一项目的事项又改了）：旧提醒（看没看过都一样）换成新的，只留一条
    prefix = key.rsplit(":", 1)[0] + ":"
    await session.execute(
        delete(UserNotification).where(
            UserNotification.org_id == org_id,
            UserNotification.dedupe_key.like(prefix + "%"), UserNotification.dedupe_key != key,
        )
    )
    for uid in users:
        stmt = insert(UserNotification).values(id=uuid.uuid4(), org_id=org_id, user_id=uid, kind=kind, dedupe_key=key, title=title, body=body, link=link)
        # 同一件事写法更新了（如简报提醒改成写明改前改后）：已有的那条也换成新写法，企业里每个账号看到的一样
        await session.execute(stmt.on_conflict_do_update(index_elements=["user_id", "dedupe_key"], set_={"title": title, "body": body, "link": link}))


async def refresh(session: AsyncSession, org_id: uuid.UUID | None) -> None:
    if org_id is None:
        return
    from app.knowledge.whitepaper import CountryWhitepaper

    users = list(
        (await session.scalars(select(User.id).where(User.org_id == org_id, User.is_active.is_(True)))).all()
    )
    if not users:
        return
    # 简报出新版：这家企业在该国有项目，且新版写于项目开始之后（排除项目侧栏派生对话 _project_side）
    started: dict[str, tuple[datetime, str]] = {}
    raw_tasks = (
        await session.execute(
            select(
                AnalysisTask.country_code,
                AnalysisTask.created_at,
                AnalysisTask.title,
                AnalysisTask.facts_json,
            ).where(AnalysisTask.org_id == org_id, AnalysisTask.archived_at.is_(None))
        )
    ).all()
    tasks = [
        (cc, created, title)
        for cc, created, title, fj in raw_tasks
        if not bool((fj or {}).get("_project_side"))
    ]
    projects = (
        await session.execute(
            select(AdmissionProject, AdmissionEntry)
            .join(AdmissionEntry, AdmissionEntry.id == AdmissionProject.entry_id)
            .where(AdmissionProject.org_id == org_id)
        )
    ).all()
    titles: dict[str, list[str]] = {}
    for code, created, title in [*tasks, *[(e.country_code, p.created_at, p.title) for p, e in projects]]:
        code = (code or "").upper()
        if code and created and (code not in started or created < started[code][0]):
            started[code] = (created, title)
        if code and title and title not in titles.setdefault(code, []):
            titles[code].append(title)
    # 简报出新版：几国一起更新的只发一条，逐国写哪段结论变了、改前改后
    fresh = []
    for code, (since, _title) in sorted(started.items()):
        latest = await session.scalar(
            select(CountryWhitepaper).where(CountryWhitepaper.country_code == code).order_by(CountryWhitepaper.version_no.desc()).limit(1)
        )
        if latest is None or latest.created_at is None or latest.created_at <= since:
            continue
        fresh.append((code, latest, await _changed_conclusions(session, latest)))
    if fresh:
        from sqlalchemy import delete

        key = "whitepaper:all:" + hashlib.sha256("|".join(f"{c}:{w.version_no}" for c, w, _ in fresh).encode()).hexdigest()[:20]
        lines = []
        for code, _w, changes in fresh:
            # 写出变成了什么：哪一部分的结论在首个差异点前后由什么改为什么
            what = "；".join(
                f"《{part}》结论由 {b} 改为 {n}" if b else f"《{part}》新增结论：{n}"
                for part, b, n in changes
            )
            mine_all = sorted(titles.get(code, []))
            mine = "、".join(mine_all[:3]) + (f"等 {len(mine_all)} 个" if len(mine_all) > 3 else "")
            lines.append(f"{_name(code)}：{what or '各部分结论没有变化，补充了依据条文'}。涉及{_name(code)}的项目：{mine}，打开简报对照新结论核对。")
        await session.execute(
            delete(UserNotification).where(UserNotification.org_id == org_id, UserNotification.kind == "whitepaper", UserNotification.dedupe_key != key)
        )
        await _send(
            session, org_id, users, kind="whitepaper", key=key,
            title="、".join(_name(c) for c, _, _ in fresh) + "合规简报已更新",
            body="\n".join(lines), link=f"/knowledge?country={fresh[0][0]}",
        )
    # 准入事项有变化：和项目上次看到的事项内容比，写明改了哪一栏、改前改后；没有实质变化不提醒
    from app.admission import filing
    from app.admission.models import AdmissionDraft

    for project, entry in projects:
        merged = False
        if entry.merged_into:
            target = await session.get(AdmissionEntry, entry.merged_into)
            if target is not None:
                entry, merged = target, True
                project.entry_id = target.id  # 事项并入另一项：项目改挂主事项（申请书、清单都按主事项生成）
        # 项目默认名称从事项来（国家 · 事项名）；用户自己起过名字的保留原名
        seen = (project.milestones or {}).get("entry_seen")
        title = f"{_name(entry.country_code)} · {entry.name_zh}"
        auto_before = f"{_name(entry.country_code)} · {seen.get('name', '')}" if seen else ""
        if not project.title or (seen and project.title == auto_before and project.title != title):
            project.title = title
        now = await entry_snapshot(session, entry)
        if merged and seen and seen.get("name") != now.get("name"):
            project.milestones = {**(project.milestones or {}), "entry_seen": now}
            continue
        # 只改了事项名称（统一写法）不算实质变化
        changes = [c for c in (entry_changes(seen, now) if seen else []) if not c.startswith("事项名称：")]
        listed_before = (seen or {}).get("listed", True)
        if seen is None or changes or now["listed"] != listed_before or seen != now:
            project.milestones = {**(project.milestones or {}), "entry_seen": now}
        if seen is None or (not changes and now["listed"] == listed_before):
            continue  # 第一次：记下现状；只是措辞、标点的变化：不提醒
        digest = hashlib.sha256(json.dumps(now, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        project.entry_seen_hash = digest
        who = f"贵司关联准入项目《{project.title}》"
        state = filing.state_of(project)
        drafted = bool(await session.scalar(select(AdmissionDraft.id).where(AdmissionDraft.project_id == project.id).limit(1)))
        if not now["listed"]:
            what = f"经平台法规核验，该事项已移出{_name(entry.country_code)}现行法定准入目录；项目既有填报信息与历史归档材料予以保留。"
        else:
            what = "本次核定变更要点：" + "；".join(changes) + "。"
            if state == "approved":
                what += "既有获批文书效力不受影响，后续展期或变更申报请按最新要求执行。"
            elif state in ("submitted", "correcting"):
                what += "已递交案卷继续审理；如监管机关通知补正，请按最新材料要件补充。"
            elif drafted:
                what += "已生成的申报材料系依据修订前口径编制，打开项目重新生成。"
            else:
                what += "打开项目按新要求准备。"
        await _send(
            session, org_id, users, kind="admission", key=f"admission:{project.id}:{digest[:20]}",
            title=f"准入事项有变化：{entry.name_zh}", body=f"{who}所依据的监管准入要求已更新。{what}", link=f"/admission?project={project.id}",
        )
    await sync_accounts(session, org_id, users)
    await session.flush()


async def sync_accounts(session: AsyncSession, org_id: uuid.UUID, users: list[uuid.UUID]) -> None:
    """Every account of the enterprise sees the same notices: one an account is missing (opened
    after the notice was sent) is copied from the others, with the same date."""
    rows = (
        await session.scalars(
            select(UserNotification).where(
                UserNotification.org_id == org_id, UserNotification.kind.in_(("whitepaper", "admission", "subscription"))
            ).order_by(UserNotification.created_at)
        )
    ).all()
    have = {(r.user_id, r.dedupe_key) for r in rows}
    first: dict[str, datetime] = {}
    for r in rows:
        first.setdefault(r.dedupe_key, r.created_at)
    for r in rows:
        if r.created_at != first[r.dedupe_key]:
            r.created_at = first[r.dedupe_key]  # 同一件事各账号同一时间，列表顺序一致
    seen: set[str] = set()
    for r in rows:
        if r.dedupe_key in seen:
            continue
        seen.add(r.dedupe_key)
        for uid in users:
            if (uid, r.dedupe_key) not in have:
                await session.execute(
                    insert(UserNotification)
                    .values(id=uuid.uuid4(), org_id=org_id, user_id=uid, kind=r.kind, change_id=r.change_id, dedupe_key=r.dedupe_key,
                            title=r.title, body=r.body, link=r.link, created_at=r.created_at)
                    .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
                )

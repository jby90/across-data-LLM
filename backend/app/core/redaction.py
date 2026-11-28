# ruff: noqa: E501
"""Reversible in-memory redaction. Mappings never belong in logs or audit rows."""

import ipaddress
import re
import secrets
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class EgressContext:
    org_id: object = None
    aliases: tuple[str, ...] = ()
    session_factory: object = None


_EMPTY_CONTEXT = EgressContext()
egress_context = ContextVar("llm_egress", default=_EMPTY_CONTEXT)


@contextmanager
def llm_scope(*, org_id=None, aliases=(), session_factory=None):
    token = egress_context.set(EgressContext(org_id, tuple(aliases), session_factory))
    try:
        yield
    finally:
        egress_context.reset(token)


def org_redaction_aliases(org=None) -> tuple[str, ...]:
    if org is None:
        return ()
    from app.iam.service import strip_trial_org_suffix

    raw_name = (getattr(org, "name", "") or "").strip()
    base_name = strip_trial_org_suffix(raw_name)
    items: list[str] = []
    if raw_name:
        items.append(raw_name)
    if base_name and len(base_name) >= 2 and base_name not in items:
        items.append(base_name)
    for term in getattr(org, "llm_redaction_terms", None) or []:
        t = str(term or "").strip()
        if t and t not in items:
            items.append(t)
    return tuple(sorted(items, key=len, reverse=True))


def bind_egress(session, org=None):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    egress_context.set(
        EgressContext(
            org.id if org else None,
            org_redaction_aliases(org),
            async_sessionmaker(session.bind, expire_on_commit=False),
        )
    )


class Redactor:
    def __init__(self, aliases=(), *, prefix=None):
        self.prefix = prefix or ("__CYSH_SECRET_" + secrets.token_hex(8).upper() + "_")
        self.mapping = {}
        self.counts = Counter()
        self.aliases = sorted(set(a.strip() for a in aliases if a.strip()), key=len, reverse=True)
        self.patterns = [
            ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w-])")),
            ("ID", re.compile(r"(?<!\d)\d{17}[\dXx](?!\w)|(?<!\w)[STFGM]\d{7}[A-Z](?!\w)")),
            (
                "VIN",
                re.compile(
                    r"(?<![A-Za-z0-9])(?=[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9]))"
                    r"(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])(?=[A-HJ-NPR-Z0-9]*\d)"
                    r"[A-HJ-NPR-Z0-9]{17}"
                ),
            ),
            (
                "PLATE",
                re.compile(
                    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
                    r"[A-Z][A-Z0-9]{5,6}(?![A-Z0-9])"
                ),
            ),
            (
                "BANK",
                # 银行卡号：连续 16–19 位，或 4 位一组的 4–5 组。不能用“任意数字夹空格或连字符”，
                # 否则表格里的年份区间、条号串（如 1986-1988 1986-1988）会被当成卡号
                re.compile(r"(?<![\d.-])(?:\d{16,19}|\d{4}(?:[ -]\d{4}){3,4})(?![\d.-])"),
            ),
            (
                "PHONE",
                re.compile(
                    r"(?<!\w)(?:\+?86[ -]?)?1[3-9](?:[ -]?\d){9}(?!\w)|"
                    r"(?<!\w)\+\d{1,3}[ -]?(?:\d[ -]?){5,13}\d(?!\w)|"
                    r"(?<!\w)\d{4}[ -]\d{4}(?!\w)"
                ),
            ),
            (
                "IP",
                re.compile(
                    r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|"
                    r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])"
                ),
            ),
        ]

    def token(self, value, kind):
        existing = next((k for k, v in self.mapping.items() if v == value), None)
        self.counts[kind] += 1
        if existing:
            return existing
        # Alphabetic suffixes avoid numeric-protection transforms in translation.
        n, suffix = len(self.mapping), ""
        while True:
            n, r = divmod(n, 26)
            suffix = chr(65 + r) + suffix
            if not n:
                break
        marker = self.prefix + suffix + "__"
        self.mapping[marker] = value
        return marker

    def redact(self, text):
        # A single substitution over the original avoids re-matching placeholders.
        rules = [("ORG", re.compile("|".join(map(re.escape, self.aliases)), re.I))]
        if not self.aliases:
            rules = []
        rules += self.patterns
        spans = []
        for priority, (kind, pattern) in enumerate(rules):
            for m in pattern.finditer(text):
                if kind == "IP":
                    try:
                        ipaddress.ip_address(m.group())
                    except ValueError:
                        continue
                    # 条号如 5.1.2.2 的写法与 IPv4 相同；各段都很小的是条号，不是地址
                    parts = m.group().split(".")
                    if len(parts) == 4 and all(p.isdigit() and int(p) <= 20 for p in parts):
                        continue
                if kind == "PHONE" and re.fullmatch(r"(?:19|20)\d{2}[ -](?:19|20)\d{2}", m.group()):
                    continue  # 年份区间（1986-1988），不是 8 位电话号码
                spans.append((m.start(), priority, -len(m.group()), m.end(), kind, m.group()))
        end, parts = 0, []
        for start, _, _, stop, kind, value in sorted(spans):
            if start < end:
                continue
            parts.extend((text[end:start], self.token(value, kind)))
            end = stop
        return "".join(parts) + text[end:]

    def restore(self, value):
        if isinstance(value, str):
            for marker, original in self.mapping.items():
                value = value.replace(marker, original)
            own_core = self.prefix.strip("_")
            if (
                self.prefix in value
                or (own_core and re.search(re.escape(own_core), value, re.I))
                or (self.mapping and re.search(r"CYSH_SECRET", value, re.I))
            ):
                raise ValueError("模型返回的隐私占位符损坏，禁止作为原文使用")
            return value
        if isinstance(value, list):
            return [self.restore(v) for v in value]
        if isinstance(value, dict):
            return {(self.restore(k) if isinstance(k, str) else k): self.restore(v) for k, v in value.items()}
        return value

    def tree(self, value):
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.tree(v) for v in value]
        if isinstance(value, dict):
            return {(self.redact(k) if isinstance(k, str) else k): self.tree(v) for k, v in value.items()}
        return value


def redact(text, aliases=()):
    r = Redactor(aliases)
    return r.redact(text), r.mapping


KIND_LABELS_ZH = {
    "ORG": "企业主体与自定义敏感词",
    "EMAIL": "电子邮箱",
    "ID": "身份证件号",
    "VIN": "车辆识别代号",
    "PLATE": "车牌号",
    "BANK": "银行账户号",
    "PHONE": "联系电话",
    "IP": "网络地址",
}


def summarize_redaction(text: str, aliases: tuple[str, ...] = ()) -> dict:
    """In-memory count-only summary of outbound LLM redaction; never returns plaintext mappings."""
    clean_aliases = {a.strip() for a in aliases if a and a.strip()}
    if not text or not text.strip():
        return {"total": 0, "by_kind": {}, "labels": [], "custom_terms_configured": len(clean_aliases)}
    r = Redactor(aliases)
    r.redact(text)
    by_kind = {k: int(v) for k, v in r.counts.items() if v}
    labels = [f"{KIND_LABELS_ZH.get(k, k)} {v} 处" for k, v in by_kind.items()]
    return {
        "total": int(sum(by_kind.values())),
        "by_kind": by_kind,
        "labels": labels,
        "custom_terms_configured": len(clean_aliases),
    }


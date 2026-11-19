# ruff: noqa: E501
"""One admission entry per procedure.

Entries are made one per admission relation of the graph, and a law states the same procedure in
several articles (the licence to process data in the general rule, in the article on fees, in the
article on the application form…), so a country can show the same licence five times under
slightly different names — sometimes under two categories. For each country the model reads all
names and summaries at once and groups the entries a company would file as one application at the
same authority; in each group the entry with the most steps stays, the others point to it
(``merged_into``) and leave the user's list. Their legal bases are shown on the remaining entry.
Nothing is deleted: an administrator sees every entry and can undo a merge by clearing
``merged_into``.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission.models import AdmissionEntry, AdmissionStep
from app.analysis.llm import LlmUnavailableError, call_llm_json

SHOWN = ("pending_review", "approved")
SYSTEM = (
    "你在整理一个国家的“市场准入与申报事项”清单，读者是要去办理的企业。清单是从法规的不同条文里逐条摘出来的，"
    "同一件要办的事（向同一主管机构申请的同一个许可、申报、认证或批准）常常被摘了好几次，名称说法不同、分类也可能不同。\n"
    "请站在办理人的角度：企业会作为同一份申请去办的，归成一组。例如“个人数据处理许可”“控制者/处理者许可证”“数据保护许可证申请”，"
    "如果都是向同一机构取得的同一个数据处理许可，就是一组；“通信设备进口许可”“通信设备进口/制造许可证”是一组。\n"
    "不同的事不要合并：处理数据的许可与跨境传输的许可是两件；首次申请与变更、续展是两件；不同机构受理的是两件；"
    "车辆型式认证与无线电设备认证是两件。\n"
    "每组再给一个能概括整组的事项名称（10–24 字，用办理人熟悉的说法，如“个人数据处理许可证”）。\n"
    '输出 JSON：{"groups":[{"items":[序号,序号,...],"name":"事项名称"}, ...]}，只列有两项及以上的组，序号与输入一致。'
)


def union_groups(groups: list[list[int]], n: int) -> list[list[int]]:
    """Groups that share an item are one group (the model may list one entry in two groups)."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for g in groups:
        for a, b in zip(g, g[1:], strict=False):
            parent[find(a)] = find(b)
    out: dict[int, set[int]] = defaultdict(set)
    for g in groups:
        for i in g:
            out[find(i)].add(i)
    return [sorted(v) for v in out.values() if len(v) > 1]


async def _live(session: AsyncSession, country_code: str) -> list[AdmissionEntry]:
    return list(
        (
            await session.scalars(
                select(AdmissionEntry).where(
                    AdmissionEntry.country_code == country_code.upper(),
                    AdmissionEntry.review_status.in_(SHOWN),
                    AdmissionEntry.merged_into.is_(None),
                )
            )
        ).all()
    )


async def dedupe(session: AsyncSession, country_code: str, passes: int = 2) -> dict:
    """Merge duplicate entries of one country. Repeatable; a second pass catches what the first left."""
    stats = {"entries": 0, "merged": 0, "groups": 0, "tokens": 0}
    for _ in range(passes):
        items = await _live(session, country_code)
        stats["entries"] = stats["entries"] or len(items)
        if len(items) < 2:
            break
        steps = dict(
            (
                await session.execute(
                    select(AdmissionStep.entry_id, func.count(AdmissionStep.id))
                    .where(AdmissionStep.entry_id.in_([e.id for e in items]))
                    .group_by(AdmissionStep.entry_id)
                )
            ).all()
        )
        listing = "\n".join(
            f"{i}. [{e.category}] {e.name_zh}｜受理：{e.authority_zh or '未写明'}｜{(e.summary_zh or '')[:120]}"
            for i, e in enumerate(items)
        )
        try:
            res = await call_llm_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": listing}], max_tokens=12000, temperature=0.0
            )
        except LlmUnavailableError:
            break
        stats["tokens"] += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        raw = (res.data or {}).get("groups", []) if isinstance(res.data, dict) else []
        groups, names = [], {}
        for g in raw:
            members = g.get("items") if isinstance(g, dict) else g
            if not isinstance(members, list):
                continue
            idx = [int(i) for i in members if str(i).isdigit() and 0 <= int(i) < len(items)]
            groups.append(idx)
            name = str(g.get("name") or "").strip() if isinstance(g, dict) else ""
            if 4 <= len(name) <= 40:
                names.update(dict.fromkeys(idx, name))
        merged_now = 0
        for idx in union_groups([g for g in groups if len(g) > 1], len(items)):
            members = [items[i] for i in idx]
            # 管理员改过的那一项优先留下（它的内容是人核过的，不能被并掉）
            keep = max(members, key=lambda e: (bool(e.edited), steps.get(e.id, 0), len(e.summary_zh or ""), e.review_status == "approved"))
            gone = [e for e in members if e is not keep]
            # 留下的那一项用整组的名称（各条目原名从单条条文摘出，可能只说了其中一种情形）
            if names.get(idx[0]) and "name_zh" not in (keep.edited or []):  # 管理员改过的名称不动
                keep.name_zh = names[idx[0]]
            for e in gone:
                e.merged_into = keep.id
            await session.flush()
            # 早先并入这些项的，改指向留下的那一项（始终只隔一层）
            for child in (
                await session.scalars(select(AdmissionEntry).where(AdmissionEntry.merged_into.in_([e.id for e in gone])))
            ).all():
                child.merged_into = keep.id
            merged_now += len(gone)
            stats["groups"] += 1
        stats["merged"] += merged_now
        await session.commit()
        if not merged_now:
            break
    return stats


async def merged_bases(session: AsyncSession, entry_id) -> list:
    """Evidence clauses of the entries merged into this one."""
    return list(
        (
            await session.scalars(
                select(AdmissionEntry.evidence_clause_id).where(
                    AdmissionEntry.merged_into == entry_id, AdmissionEntry.evidence_clause_id.is_not(None)
                )
            )
        ).all()
    )

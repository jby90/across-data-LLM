# ruff: noqa: E501
"""Which of the enterprise's compliance documents cite an article — the other direction of the
article cards beside the questions, for the regulations library (an article shows the documents
that rest on it, and each one links back there).

Three places cite an article, all compared by the article's clause id (never by its name):
- the card beside a question (``question_basis``): the question, in every document that asks it;
- the body of a document: the clauses its “依据” tables put in (``Rendered.cited``);
- a written argumentative part: the clauses the paper cites (kept with the paper).
A document produced on an official template (data leaving China: declaration, risk report, DPIA)
carries neither the platform's tables nor the paper, so only its questions count.
Only the enterprise's own open projects are read.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def for_document(session: AsyncSession, org_id: uuid.UUID | None, document_id: uuid.UUID) -> dict[str, list[dict]]:
    """clause id (of this regulation) → the places in the enterprise's documents that cite it."""
    from app.analysis.doc_projects import build_overview
    from app.analysis.models import AnalysisTask
    from app.ingestion.models import Clause, DocumentVersion

    if org_id is None:
        return {}
    tasks = (
        await session.scalars(
            select(AnalysisTask)
            .where(AnalysisTask.org_id == org_id, AnalysisTask.kind == "documents", AnalysisTask.archived_at.is_(None))
            .order_by(AnalysisTask.updated_at.desc())
        )
    ).all()
    found: dict[str, list[dict]] = {}

    def add(clause_id: str, ref: dict) -> None:
        refs = found.setdefault(str(clause_id), [])
        if ref not in refs:
            refs.append(ref)

    for task in tasks:
        view = await build_overview(session, task)
        names = {d["code"]: d["name"] for d in view["documents"]}
        base = {"project_id": str(task.id), "project_title": task.title}
        for q in view["questions"]:
            for card in q.get("basis") or []:
                if card.get("document_id") != str(document_id) or not card.get("clause_id"):
                    continue
                for code in q.get("used_in") or []:
                    add(card["clause_id"], {**base, "doc_code": code, "doc_name": names.get(code, code), "where": "question",
                                            "question_key": q["key"], "question_label": q["label"],
                                            "link": f"/documents?project={task.id}&q={q['key']}"})
        for d in view["documents"]:
            for c in d.get("cited") or []:
                add(c["clause_id"], {**base, "doc_code": d["code"], "doc_name": d["name"], "where": c["where"],
                                     "section": c.get("section", ""), "link": f"/documents?project={task.id}&doc={d['code']}"})
    if not found:
        return {}
    # 正文和论述里的条文只知道 id：只留属于这部法规的
    ids = [uuid.UUID(i) for i in found]
    mine = {
        str(i)
        for i in (
            await session.scalars(
                select(Clause.id).join(DocumentVersion, DocumentVersion.id == Clause.version_id).where(
                    Clause.id.in_(ids), DocumentVersion.document_id == document_id
                )
            )
        ).all()
    }
    return {k: v for k, v in found.items() if k in mine}

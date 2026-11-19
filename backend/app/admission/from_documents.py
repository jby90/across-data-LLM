# ruff: noqa: E501
"""Admission items read straight from a law's clauses.

Admission items are normally made from the graph's “准入要求” relations. Some laws that lay down an
admission (a vehicle-homologation decree, an equipment-registration guide) came into the library
after the graph was built, or their admission clauses were not picked as relations, so the country
has no item for it — e.g. no vehicle type approval. This reads the clauses of given documents that
speak of an approval, homologation, registration, licence or authorisation, and makes items with the
same extraction and verbatim checks as the relation path (``generator.extract_entry``). Clauses that
already carry an item are skipped; the result then goes through the same merge of duplicates.
"""

from __future__ import annotations

import asyncio
import re
import uuid

from sqlalchemy import select

from app.admission.generator import extract_entry
from app.admission.models import AdmissionEntry, AdmissionStep
from app.ingestion.models import Clause, Document, DocumentVersion

# 各语种里“须经批准/认可/登记/许可”的说法（法、英、阿）
ADMISSION_WORDS = re.compile(
    r"homolog|réception (par type|à titre isolé|des véhicules)|agrément|certificat de conformit|"
    r"type[- ]approval|equipment registration|register(ed)? with|immatricul|registration of (a |the )?(motor )?vehicle|"
    r"اعتماد الطراز|المصادقة على|موافقة النوع|تسجيل المركبة|تسجيل السيارة|تسجيل الآلية|الموافقة المسبقة",
    re.IGNORECASE,
)
# 驾驶执照、罚款这类不是车辆或设备准入
NOT_ADMISSION = re.compile(r"permis de conduire|driving licen|رخصة السوق|رخصة القيادة|amende|غرامة", re.IGNORECASE)
DUTY_WORDS = re.compile(r"\b(must|shall|doit|doivent|est soumis|sont soumis|required|requis|obligatoire)\b|يجب|يتعين|يلزم|لا يجوز", re.IGNORECASE)


def _key_sentence(text: str) -> str:
    flat = " ".join(text.split())
    for part in re.split(r"(?<=[.;:。؛])\s+", flat):
        if ADMISSION_WORDS.search(part) and DUTY_WORDS.search(part):
            return part[:400]
    m = ADMISSION_WORDS.search(flat)
    return flat[max(0, m.start() - 150) : m.start() + 250] if m else flat[:400]


async def from_documents(sf, document_ids: list[uuid.UUID], *, concurrency: int = 6, pattern: str | None = None) -> dict:
    """``pattern``: read only the clauses matching this expression (no duty wording needed) — for a
    law whose admission article is worded as what the authority does (“the administration approves
    the vehicle”) or whose text layer splits the usual words."""
    import app.models_registry  # noqa: F401  模型调用要写出站审计，需全部表已登记
    from app.core.rls import system_scope

    stats = {"clauses": 0, "entries": 0, "out_of_scope": 0, "failed": 0, "skipped": 0, "names": []}
    with system_scope("admission-from-documents"):
        async with sf() as s:
            rows = (
                await s.execute(
                    select(Clause, Document.country_code)
                    .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(Document.id.in_(document_ids))
                    .order_by(Document.id, Clause.ordinal)
                )
            ).all()
            done = {
                c for c in (await s.scalars(select(AdmissionEntry.evidence_clause_id).where(AdmissionEntry.evidence_clause_id.is_not(None)))).all()
            }
    picked = []
    for clause, cc in rows:
        text = clause.original_text or ""
        if clause.id in done:
            stats["skipped"] += 1
            continue
        if pattern:
            if re.search(pattern, text) and len(text) > 60:
                picked.append((clause, cc))
        elif ADMISSION_WORDS.search(text) and DUTY_WORDS.search(text) and not NOT_ADMISSION.search(text[:600]) and len(text) > 60:
            picked.append((clause, cc))
    stats["clauses"] = len(picked)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(clause: Clause, cc: str):
        quote = _key_sentence(clause.original_text)
        async with gate:
            with system_scope("admission-from-documents"):
                status, entry = await extract_entry(cc, "", quote, clause.original_text)
        if status == "failed":
            stats["failed"] += 1
            return
        if status == "out_of_scope":
            stats["out_of_scope"] += 1
            return
        with system_scope("admission-from-documents"):
            async with sf() as s:
                item = AdmissionEntry(
                    country_code=cc, name_zh=entry["name_zh"], category=entry["category"], authority_zh=entry["authority_zh"],
                    summary_zh=entry["summary_zh"], evidence_clause_id=clause.id, evidence_quote=quote[:600], review_status="pending_review",
                )
                s.add(item)
                await s.flush()
                for seq, st in enumerate(entry["steps"], 1):
                    s.add(AdmissionStep(entry_id=item.id, seq=seq, title_zh=st["title_zh"], detail_zh=st["detail_zh"],
                                        materials=st["materials"], evidence_clause_id=clause.id, evidence_quote=st["quote"]))
                await s.commit()
        stats["entries"] += 1
        stats["names"].append(f"{cc} {entry['name_zh']}")

    await asyncio.gather(*(one(c, cc) for c, cc in picked))
    return stats

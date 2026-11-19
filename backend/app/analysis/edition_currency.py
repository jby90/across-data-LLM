"""Which documents are current editions, and how much weight an instrument kind carries.

Data-driven and country-neutral: a document marked as the BASE text of an instrument family
is superseded when another BASE document of the same family *and the same title* (an older
edition of the same text, not a different instrument filed under the family) has a later
publication date.
Nothing is deleted or rewritten; superseded documents are only left out of citation
candidates, and the caller records which ones and why. Missing family, kind or date means
"cannot tell", never "superseded".
"""

from collections.abc import Iterable

BASE_KIND = "BASE"

# Non-binding or outdated commentary ranks below the binding text it explains when both
# match equally well. Kinds not listed here keep full weight.
# 法律位阶：指南、说明类文件排在法律、法规之后；同样的字面命中，主体法优先
INSTRUMENT_KIND_WEIGHTS = {
    "GUIDANCE": 0.8,
    "REGULATOR_GUIDANCE": 0.8,
    "ADMISSION_GUIDANCE": 0.8,
    "HISTORICAL_GUIDANCE": 0.5,
}


def superseded_documents(
    entries: Iterable[tuple[str, str | None, str | None, str | None, object]],
):
    """entries: (document_id, instrument_family, instrument_kind, title, published_date)."""
    editions: dict[tuple[str, str], dict[str, object]] = {}
    for doc_id, family, kind, title, published in entries:
        if family and kind == BASE_KIND and title and published:
            editions.setdefault((family, " ".join(title.split()).casefold()), {})[doc_id] = (
                published
            )
    out: dict[str, dict] = {}
    for (family, _title), docs in editions.items():
        newest = max(docs.values())
        for doc_id, published in docs.items():
            if published < newest:
                out[doc_id] = {
                    "instrument_family": family,
                    "published_date": str(published),
                    "superseded_by_edition": str(newest),
                }
    return out


def kind_weight(instrument_kind: str | None) -> float:
    return INSTRUMENT_KIND_WEIGHTS.get(instrument_kind or "", 1.0)

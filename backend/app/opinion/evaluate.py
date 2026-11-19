"""Measure the opinion grading against reference labels, with a dev set and a test set.

Every collected item is part of the evaluation (no hand-picking). Items are split into a
development part, on which the extraction prompt may be tuned, and a test part that is only
looked at to report the figure. The split is fixed by a hash of the item's address, so it does not
move when more items are collected.
Reported: overall accuracy, per-grade recall (a missed high is worse than a false alarm), and
the confusion matrix; also the figures on the items the two labelers agreed on.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

LEVELS = ("high", "medium", "low")
DEV_SHARE = 4  # items whose hash bucket is below this (out of 10) are development items


def split_of(url: str) -> str:
    bucket = int(hashlib.sha256(url.encode()).hexdigest(), 16) % 10
    return "dev" if bucket < DEV_SHARE else "test"


def metrics(rows: list[dict]) -> dict:
    """rows: {"final": level, "predicted": level, "agreed": bool}"""
    out: dict = {"n": len(rows)}
    if not rows:
        return out
    correct = sum(1 for r in rows if r["final"] == r["predicted"])
    out["accuracy"] = correct / len(rows)
    out["confusion"] = {
        f"{t}->{p}": c
        for (t, p), c in sorted(Counter((r["final"], r["predicted"]) for r in rows).items())
    }
    out["recall"] = {}
    for level in LEVELS:
        truth = [r for r in rows if r["final"] == level]
        if truth:
            out["recall"][level] = {
                "n": len(truth),
                "recall": sum(1 for r in truth if r["predicted"] == level) / len(truth),
            }
    agreed = [r for r in rows if r.get("agreed")]
    if agreed:
        out["agreed_n"] = len(agreed)
        out["agreed_accuracy"] = sum(1 for r in agreed if r["final"] == r["predicted"]) / len(
            agreed
        )
    return out


def report(rows: list[dict]) -> dict:
    return {
        "all": metrics(rows),
        "dev": metrics([r for r in rows if r["split"] == "dev"]),
        "test": metrics([r for r in rows if r["split"] == "test"]),
    }


async def label_all(
    sf, out_path: Path, *, model_a: str, model_b: str, model_judge: str, concurrency: int = 4
):
    """Label every collected item (resumable: items already in the file are skipped)."""
    from sqlalchemy import select

    from app.opinion.labeling import label_with, rule_between
    from app.opinion.models import OpinionItem

    done: dict[str, dict] = {}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["url"]] = row
    async with sf() as session:
        items = (
            await session.execute(
                select(
                    OpinionItem.id,
                    OpinionItem.url,
                    OpinionItem.title,
                    OpinionItem.body,
                    OpinionItem.content_hash,
                    OpinionItem.publisher,
                ).order_by(OpinionItem.url)
            )
        ).all()
    todo = [i for i in items if i.url not in done]
    gate = asyncio.Semaphore(max(1, concurrency))
    lock = asyncio.Lock()
    stats = {"labelled": 0, "agreed": 0, "ruled": 0, "failed": 0}

    async def one(item):
        async with gate:
            a, b = await asyncio.gather(
                label_with(model_a, item.title, item.body),
                label_with(model_b, item.title, item.body),
            )
            if a is None and b is None:
                stats["failed"] += 1
                return
            if a and b and a["level"] == b["level"]:
                final, agreed, ruling = a, True, None
            elif a and b:
                ruling = await rule_between(model_judge, item.title, item.body, a, b)
                final, agreed = ruling or a, False
            else:
                final, agreed, ruling = a or b, False, None
            row = {
                "item_id": str(item.id),
                "url": item.url,
                "title": item.title,
                "publisher": item.publisher,
                "content_hash": item.content_hash,
                "split": split_of(item.url),
                "label_a": a,
                "label_b": b,
                "ruling": ruling,
                "final": final["level"],
                "agreed": agreed,
            }
            async with lock:
                with out_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            stats["labelled"] += 1
            stats["agreed" if agreed else "ruled"] += 1

    await asyncio.gather(*(one(i) for i in todo))
    return stats


async def arbitrate_conflicts(sf, labels_path: Path, *, model: str, concurrency: int = 3) -> dict:
    """For every item where the system's grade differs from the reference label, ask for a final
    decision from the written specification (resumable; decided items are kept).

    The system and the reference are independent readings of the same specification, so neither
    outranks the other: the specification does. The decision is stored next to both, so the figure
    can be reported with and without it."""
    from sqlalchemy import select

    from app.opinion.labeling import arbitrate
    from app.opinion.models import OpinionItem

    rows = [
        json.loads(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    async with sf() as session:
        items = {
            r.url: r
            for r in (
                await session.execute(
                    select(
                        OpinionItem.url,
                        OpinionItem.title,
                        OpinionItem.body,
                        OpinionItem.level,
                        OpinionItem.rules,
                        OpinionItem.facts,
                    )
                )
            ).all()
        }
    gate = asyncio.Semaphore(max(1, concurrency))
    stats = {"conflicts": 0, "decided": 0, "for_system": 0, "for_reference": 0, "neither": 0}

    async def one(row):
        item = items.get(row["url"])
        if item is None or item.level is None or item.level == row["final"]:
            row.pop("arbitration", None)
            return
        stats["conflicts"] += 1
        if row.get("arbitration") and row["arbitration"].get("system_level") == item.level:
            decided = row["arbitration"]
        else:
            ev = (item.facts or {}).get("evidence") or {}
            system = {
                "level": item.level,
                "rule": ",".join(item.rules or []),
                "quote": " ｜ ".join(str(v)[:200] for v in ev.values())[:400],
            }
            ref = row.get("ruling") or row.get("label_a") or {}
            reference = {k: ref.get(k, "") for k in ("level", "rule", "quote")}
            pair = [system, reference]
            order = (
                int(hashlib.sha256(row["url"].encode()).hexdigest(), 16) % 2
            )  # 位置不偏向任何一方
            first, second = (pair[0], pair[1]) if order == 0 else (pair[1], pair[0])
            async with gate:
                verdict = await arbitrate(model, item.title, item.body, first, second)
            if verdict is None:
                return
            decided = {"system_level": item.level, **verdict}
        row["arbitration"] = decided
        stats["decided"] += 1
        stats[
            "for_system"
            if decided["level"] == item.level
            else "for_reference"
            if decided["level"] == row["final"]
            else "neither"
        ] += 1

    await asyncio.gather(*(one(r) for r in rows))
    labels_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    return stats


async def evaluate(sf, labels_path: Path, *, use_arbitration: bool = True) -> dict:
    """Compare the stored grades with the reference labels."""
    from sqlalchemy import select

    from app.opinion.models import OpinionItem

    labels = [
        json.loads(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    async with sf() as session:
        graded = {
            url: level
            for url, level in (
                await session.execute(select(OpinionItem.url, OpinionItem.level))
            ).all()
        }
    rows = []
    skipped = 0
    for lab in labels:
        predicted = graded.get(lab["url"])
        if predicted is None:
            skipped += 1
            continue
        rows.append(
            {
                "final": (lab.get("arbitration") or {}).get("level", lab["final"])
                if use_arbitration
                else lab["final"],
                "predicted": predicted,
                "agreed": lab["agreed"],
                "split": lab["split"],
                "title": lab["title"],
                "url": lab["url"],
            }
        )
    result = report(rows)
    result["not_graded"] = skipped
    result["label_distribution"] = dict(Counter(r["final"] for r in rows))
    return result

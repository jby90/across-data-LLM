"""Regression tests for country law/requirement count consistency and strict separation of
regulatory agencies from regulation titles (2026-11-17)."""

from __future__ import annotations

from app.knowledge import whitepaper_graph as wg

from .conftest import bearer, login
from .test_analysis_p3 import _seed_sg_corpus


def test_bilingual_regulator_and_duplicate_key_hygiene():
    assert wg._plain("الأشخاص الاعتبارية（法人）") == "法人"
    assert wg._plain("كل شخص（任何人）") == "任何人"
    assert wg._plain("organisation（组织）") == "组织"
    assert wg._plain("组织（X）") == "组织"
    assert wg._display_regulator_name("黎巴嫩经济与贸易部长") == "黎巴嫩经济与贸易部"
    assert wg._display_regulator_name("部长会议") == "部长会议"
    assert wg.UNNAMED_ROLE.search("黎巴嫩内政与市政部长和农业部长")
    assert wg.UNNAMED_ROLE.search("黎巴嫩国家协商委员会")
    assert wg.UNNAMED_ROLE.search("主管部长")
    assert wg._key("图像保留期限不超过三个月") == wg._key("图像保存期限不得超过三个月")
    assert wg._key("图像保存期限不超过3个月") == wg._key("图像保存期限不超过三个月")


async def test_country_briefing_separates_regulators_and_matches_every_dimension_count(
    client, seed, session_factory
):
    from sqlalchemy import select

    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge import whitepaper_router
    from app.knowledge.models import KgNode

    await _seed_sg_corpus(session_factory)
    async with session_factory() as s:
        doc = (await s.scalars(select(Document).where(Document.country_code == "SG"))).first()
        clause = (
            await s.scalars(
                select(Clause)
                .join(DocumentVersion, Clause.version_id == DocumentVersion.id)
                .where(DocumentVersion.document_id == doc.id)
            )
        ).first()
        # Seed >12 distinct requirements in D5 + an obligation-with-penalty + a regulator
        nodes = [
            KgNode(
                country_code="SG",
                node_key=f"sec_{idx}",
                node_type="OBLIGATION",
                name_zh=f"车载数据安全存储控制措施第{idx}项",
                document_id=doc.id,
                evidence_clause_id=clause.id,
                verify_state="auto",
                attributes={
                    "node_class": "concrete",
                    "ontology": {},
                    "claim_tag": {"aspect": "security", "close": "vehicle"},
                },
            )
            for idx in range(1, 16)
        ]
        nodes.append(
            KgNode(
                country_code="SG",
                node_key="obl_pen_1",
                node_type="OBLIGATION",
                name_zh="获取事前许可义务",
                document_id=doc.id,
                evidence_clause_id=clause.id,
                verify_state="auto",
                attributes={
                    "node_class": "concrete",
                    "ontology": {"penalty_amount": {"value": "处1万至5万新元罚金"}},
                    "claim_tag": {"aspect": "admission", "close": "data"},
                },
            )
        )
        nodes.append(
            KgNode(
                country_code="SG",
                node_key="reg_pdpc_1",
                node_type="REGULATOR",
                name_zh="新加坡个人数据保护委员会（PDPC）",
                document_id=doc.id,
                evidence_clause_id=clause.id,
                verify_state="auto",
                review_status="approved",
                attributes={"evidence_clause_ids": [str(clause.id)]},
            )
        )
        s.add_all(nodes)
        await s.commit()

    whitepaper_router._page_cache.clear()
    auth = bearer(await login(client, "user", "a-member@example.com"))
    page = (await client.get("/api/knowledge/whitepapers/SG/page", headers=auth)).json()
    adm_entries = (await client.get("/api/admission/entries?country=SG", headers=auth)).json()

    for dim in page["dimensions"]:
        # Every dimension's card count must strictly equal its expandable drawer items length
        assert dim["total"] == len(dim["items"]), (dim["code"], dim["total"], len(dim["items"]))

    d1 = next(d for d in page["dimensions"] if d["code"] == "D1")
    assert d1["total"] == page["documents"]
    assert all(it["doc_id"] for it in d1["items"])
    assert not any(it["sub"] in ("监管机构", "监管与主管机构") for it in d1["items"])
    assert isinstance(d1.get("regulators"), list) and len(d1["regulators"]) >= 1
    assert any("PDPC" in r["title"] for r in d1["regulators"])

    d5 = next(d for d in page["dimensions"] if d["code"] == "D5")
    assert d5["total"] >= 15 and len(d5["items"]) == d5["total"]

    d6 = next(d for d in page["dimensions"] if d["code"] == "D6")
    assert any(it["title"] == "违反“获取事前许可”的法律责任" for it in d6["items"])
    assert not any(it["title"] == "获取事前许可义务" for it in d6["items"])

    d7 = next(d for d in page["dimensions"] if d["code"] == "D7")
    assert d7["total"] == len(adm_entries)

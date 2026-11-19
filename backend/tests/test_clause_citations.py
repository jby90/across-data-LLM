"""第 10 条：法规条文旁显示“被本企业哪些合规文件引用”（题旁依据、正文引用、论述引用，按条文 id 比）。"""

from app.analysis import documents_router as D
from app.analysis import question_basis as QB
from app.analysis.document_narrative import cited_ids, cited_in_blocks

from .conftest import bearer, login
from .test_doc_projects import BASE, _make


async def _law(session_factory):
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.sources.models import SourceSite

    async with session_factory() as s:
        site = SourceSite(country_code="MA", publisher="CNDP", language="en", document_type="LAW", entry_url="https://cndp.example/law",
                          strategy="DIRECT", terms_status="pending", enabled=False)
        s.add(site)
        await s.flush()
        doc = Document(site_id=site.id, country_code="MA", title="Law 09-08", document_type="LAW", language="en", source_url="https://cndp.example/law")
        s.add(doc)
        await s.flush()
        v = DocumentVersion(document_id=doc.id, version_no=1, content_hash="1" * 64, archive_path="v1", mime_type="application/pdf",
                            size_bytes=1, final_url="https://cndp.example/law", state="parsed")
        s.add(v)
        await s.flush()
        c1 = Clause(version_id=v.id, ordinal=1, clause_number="43", language="en", original_text="Article 43: transfer abroad.", text_hash="a" * 64)
        c2 = Clause(version_id=v.id, ordinal=2, clause_number="44", language="en", original_text="Article 44: exceptions.", text_hash="b" * 64)
        s.add_all([c1, c2])
        await s.commit()
        return str(doc.id), str(c1.id), str(c2.id)


def test_a_papers_cited_clauses_are_found_by_id_the_way_the_paper_wrote_them():
    cits = [
        {"clause_id": "c1", "document_title": "Law 09-08", "clause_number": "43"},
        {"clause_id": "c2", "document_title": "Law 09-08", "clause_number": "44"},
        {"clause_id": "c3", "document_title": "Law 09-08 bis", "clause_number": "43"},
    ]
    assert cited_ids({("Law 09-08", "43")}, cits) == ["c1"]
    table = {"type": "table", "head": ["编号", "法规与条款", "条文摘录"], "rows": [["[1]", "Law 09-08 第44条", "..."]]}
    assert cited_in_blocks([table], cits) == ["c2"]  # 旧论述：按它自己那张表逐字对回，名称相近的不算
    assert cited_in_blocks([], cits) == []


async def test_an_article_lists_the_documents_that_cite_it_and_only_for_its_own_enterprise(client, seed, session_factory, monkeypatch):
    doc_id, c43, c44 = await _law(session_factory)

    async def basis(session, task, values):  # 正文依据：这部法的第 43 条（D3 跨境传输）
        return [{"clause_id": c43, "document_id": doc_id, "document_title": "Law 09-08", "clause_number": "43",
                 "quote_original": "Article 43: transfer abroad.", "quote_zh": "第43条：向境外传输。", "dimension_code": "D3", "side": "source"}]

    real = QB.for_project

    async def cards(session, **kw):  # 题旁依据：第 44 条挂在“出境目的”上
        out = await real(session, **kw)
        out["purpose"] = [{"law": "《Law 09-08》", "article": "第44条", "point": "例外", "country": "MA", "document_id": doc_id, "clause_id": c44}]
        return out

    monkeypatch.setattr(D, "_both_ends", basis)
    monkeypatch.setattr(QB, "for_project", cards)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics")
    got = (await client.get(f"{BASE}/cited-by", headers=auth, params={"document_id": doc_id})).json()["clauses"]
    assert set(got) == {c43, c44}
    body = got[c43]
    assert body and all(r["where"] == "body" and r["project_id"] == p["id"] and r["link"] == f"/documents?project={p['id']}&doc={r['doc_code']}" for r in body)
    purpose = next(q for q in p["questions"] if q["key"] == "purpose")
    assert {r["doc_code"] for r in got[c44]} == set(purpose["used_in"])  # 一题用在几份文件里，每份都算
    assert all(r["where"] == "question" and r["link"] == f"/documents?project={p['id']}&q=purpose" for r in got[c44])
    # 别的企业看不到这家企业的文件
    other = bearer(await login(client, "user", "b-admin@example.com"))
    assert (await client.get(f"{BASE}/cited-by", headers=other, params={"document_id": doc_id})).json()["clauses"] == {}


async def test_documents_on_chinese_official_templates_count_only_their_questions(client, seed, session_factory, monkeypatch):
    doc_id, c43, _ = await _law(session_factory)

    async def basis(session, task, values):
        return [{"clause_id": c43, "document_id": doc_id, "document_title": "Law 09-08", "clause_number": "43",
                 "quote_original": "x", "quote_zh": "x", "dimension_code": "D3", "side": "recipient"}]

    monkeypatch.setattr(D, "_both_ends", basis)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="摩洛哥", function="ota_diagnostics")
    docs = {d["code"]: d for d in p["documents"]}
    assert docs["declaration"]["cited"] == docs["risk_report"]["cited"] == docs["dpia"]["cited"] == []  # 官方原件里没有平台的依据表
    got = (await client.get(f"{BASE}/cited-by", headers=auth, params={"document_id": doc_id})).json()["clauses"]
    assert {r["doc_code"] for r in got.get(c43, [])} <= set(docs) - {"declaration", "risk_report", "dpia"}

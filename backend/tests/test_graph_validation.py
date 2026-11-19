import hashlib


def _h(text):
    return hashlib.sha256(text.encode()).hexdigest()


async def test_repoint_relocate_and_validate(session_factory):
    from sqlalchemy import select

    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.extractor import relocate_changed_evidence, repoint_unchanged_evidence
    from app.knowledge.graph_validation import retire_dangling_edges, validate_graph
    from app.knowledge.models import KgEdge, KgNode
    from app.sources.models import SourceSite

    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="X",
            language="en",
            document_type="LAW",
            entry_url="https://e.example/x",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="SG",
            title="T",
            document_type="LAW",
            language="en",
            source_url="https://e.example/x",
        )
        s.add(doc)
        await s.flush()
        versions = []
        for no in (1, 2):
            v = DocumentVersion(
                document_id=doc.id,
                version_no=no,
                content_hash=str(no) * 64,
                archive_path="a",
                mime_type="application/pdf",
                size_bytes=1,
                final_url="https://e.example/x",
                state="parsed",
            )
            s.add(v)
            await s.flush()
            versions.append(v)

        def clause(v, ordinal, number, text):
            c = Clause(
                version_id=v.id,
                ordinal=ordinal,
                clause_number=number,
                language="en",
                original_text=text,
                text_hash=_h(text),
            )
            s.add(c)
            return c

        same_old, changed_old, gone_old = (
            clause(versions[0], 1, "1", "Same text."),
            clause(versions[0], 2, "2", "Old reading of two."),
            clause(versions[0], 3, "3", "Removed."),
        )
        same_new = clause(versions[1], 1, "1", "Same text.")
        changed_new = clause(versions[1], 2, "2", "Corrected reading of two.")
        await s.flush()

        def node(name, ev, ntype="OBLIGATION", status="approved"):
            n = KgNode(
                country_code="SG",
                node_type=ntype,
                node_key=name,
                name_zh=name,
                evidence_clause_id=ev.id if ev else None,
                review_status=status,
            )
            s.add(n)
            return n

        n_same = node("same", same_old)
        n_changed = node("changed", changed_old)
        n_gone = node("gone", gone_old, status="pending_review")
        node("none", None, status="pending_review")
        node("country", None, ntype="COUNTRY")
        await s.flush()
        edge = KgEdge(
            country_code="SG",
            source_node_id=n_same.id,
            target_node_id=n_changed.id,
            relation_type="REQUIRES",
            evidence_clause_id=same_old.id,
            evidence_quote="Same text.",
            extractor_model="test",
            review_status="approved",
        )
        s.add(edge)
        await s.flush()

        stats = await repoint_unchanged_evidence(s, country="SG")
        assert stats["nodes_moved"] == 1 and stats["edges_moved"] == 1
        await s.refresh(n_same), await s.refresh(edge)
        assert n_same.evidence_clause_id == same_new.id and edge.evidence_clause_id == same_new.id
        assert n_same.review_status == "approved"

        assert (await relocate_changed_evidence(s, country="SG")) == {"nodes_relocated": 1}
        await s.refresh(n_changed)
        assert n_changed.evidence_clause_id == changed_new.id
        assert n_changed.review_status == "pending_review"  # 文字变了，须重新审
        assert n_changed.attributes["evidence_relocated"] is True

        await s.refresh(n_gone)
        assert n_gone.evidence_clause_id == gone_old.id  # 没有对应条款：不乱指

        report = {r["key"]: r for r in await validate_graph(s, "SG")}
        assert [i["name"] for i in report["no_evidence"]["items"]] == ["none"]  # COUNTRY 不查
        assert [i["name"] for i in report["old_version_evidence"]["items"]] == ["gone"]

        n_gone.review_status = "superseded"
        await s.flush()
        edge.target_node_id = n_gone.id
        await s.flush()
        assert (await retire_dangling_edges(s, "SG")) == 1
        got = (await s.scalars(select(KgEdge).where(KgEdge.id == edge.id))).one()
        assert got.review_status == "stale"


async def test_relocate_by_similarity_only_on_clear_match(session_factory):
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.extractor import relocate_by_similarity
    from app.knowledge.models import KgNode
    from app.sources.models import SourceSite

    text_a = "Article (55) The card holder shall use the card under the agreed conditions " * 3
    async with session_factory() as s:
        site = SourceSite(
            country_code="LB",
            publisher="X",
            language="en",
            document_type="LAW",
            entry_url="https://e.example/y",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="LB",
            title="T",
            document_type="LAW",
            language="en",
            source_url="https://e.example/y",
        )
        s.add(doc)
        await s.flush()
        vs = []
        for no in (1, 2):
            v = DocumentVersion(
                document_id=doc.id,
                version_no=no,
                content_hash=str(no) * 64,
                archive_path="a",
                mime_type="application/pdf",
                size_bytes=1,
                final_url="https://e.example/y",
                state="parsed",
            )
            s.add(v)
            await s.flush()
            vs.append(v)

        def clause(v, n, body):
            c = Clause(
                version_id=v.id,
                ordinal=n,
                language="en",
                original_text=body,
                text_hash=_h(body),
            )
            s.add(c)
            return c

        old = clause(vs[0], 1, text_a)
        new_good = clause(vs[1], 1, text_a.replace("shall use", "must use"))
        clause(vs[1], 2, "Completely unrelated provision about courts and fees " * 3)
        await s.flush()
        node = KgNode(
            country_code="LB",
            node_type="OBLIGATION",
            node_key="k",
            name_zh="k",
            evidence_clause_id=old.id,
            review_status="approved",
        )
        s.add(node)
        await s.flush()
        out = await relocate_by_similarity(s, country="LB")
        assert out["nodes_relocated"] == 1
        await s.refresh(node)
        assert node.evidence_clause_id == new_good.id and node.review_status == "pending_review"


async def test_restore_evidence_from_attributes_uses_quote_and_sibling_document(session_factory):
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.graph_validation import restore_evidence_from_attributes
    from app.knowledge.models import KgNode
    from app.sources.models import SourceSite

    async with session_factory() as s:
        site = SourceSite(
            country_code="MA",
            publisher="X",
            language="fr",
            document_type="LAW",
            entry_url="https://e.example/z",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        docs = []
        for title in ("Decret A", "Loi B"):
            d = Document(
                site_id=site.id,
                country_code="MA",
                title=title,
                document_type="LAW",
                language="fr",
                source_url="https://e.example/" + title[:1],
            )
            s.add(d)
            await s.flush()
            v = DocumentVersion(
                document_id=d.id,
                version_no=1,
                content_hash="c" * 64,
                archive_path="a",
                mime_type="application/pdf",
                size_bytes=1,
                final_url="https://e.example/z",
                state="parsed",
            )
            s.add(v)
            await s.flush()
            docs.append(v)

        def clause(v, n, body):
            c = Clause(
                version_id=v.id, ordinal=n, language="fr", original_text=body, text_hash=_h(body)
            )
            s.add(c)
            return c

        a1 = clause(
            docs[0],
            1,
            "Le procureur du Roi territorialement compétent en est préalablement informé.",
        )
        clause(
            docs[1],
            1,
            "Le procureur du Roi territorialement compétent en est préalablement informé.",
        )
        a2 = clause(docs[0], 2, "La demande écrite doit contenir le nom.")
        await s.flush()
        gone = "11111111-1111-1111-1111-111111111111"

        def node(name, quote):
            n = KgNode(
                country_code="MA",
                node_type="OBLIGATION",
                node_key=name,
                name_zh=name,
                name_orig=quote,
                review_status="pending_review",
                attributes={"evidence_clause_ids": [gone]},
            )
            s.add(n)
            return n

        unique = node("unique", "La demande écrite doit contenir")
        sibling = node("sibling", "procureur du Roi territorialement compétent")  # 两份文件都有
        unknown = node("unknown", "une phrase qui n'existe nulle part dans ce corpus")
        await s.flush()
        out = await restore_evidence_from_attributes(s, "MA")
        assert out == {"restored": 2, "skipped": 1}
        assert unique.evidence_clause_id == a2.id
        assert sibling.evidence_clause_id == a1.id  # 兄弟节点已在 Decret A，据此消歧
        assert unknown.evidence_clause_id is None


async def test_stale_edges_come_back_when_their_sentence_is_on_a_clause_of_the_latest_version(
    session_factory,
):
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.graph_validation import relocate_edges_by_quote
    from app.knowledge.models import KgEdge, KgNode
    from app.sources.models import SourceSite

    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="X",
            language="en",
            document_type="LAW",
            entry_url="https://e.example/q",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="SG",
            title="Q",
            document_type="LAW",
            language="en",
            source_url="https://e.example/q",
        )
        s.add(doc)
        await s.flush()
        vs = []
        for no in (1, 2):
            v = DocumentVersion(
                document_id=doc.id,
                version_no=no,
                content_hash=str(no) * 64,
                archive_path="a",
                mime_type="application/pdf",
                size_bytes=1,
                final_url="https://e.example/q",
                state="parsed",
            )
            s.add(v)
            await s.flush()
            vs.append(v)

        def clause(v, n, body):
            c = Clause(
                version_id=v.id, ordinal=n, language="en", original_text=body, text_hash=_h(body)
            )
            s.add(c)
            return c

        old = clause(
            vs[0], 1, "Old cut. The Organisation  shall notify the Commission within 3 days. More."
        )
        old2 = clause(vs[0], 2, "Old cut. A sentence that is nowhere in the new reading at all.")
        new = clause(
            vs[1],
            1,
            "New cut. The Organisation shall notify the Commission within 3 days. Other text.",
        )
        clause(vs[1], 2, "New cut. Something else entirely.")
        await s.flush()
        a = KgNode(
            country_code="SG",
            node_type="INSTRUMENT",
            node_key="a",
            name_zh="a",
            review_status="approved",
        )
        b = KgNode(
            country_code="SG",
            node_type="OBLIGATION",
            node_key="b",
            name_zh="b",
            review_status="approved",
        )
        s.add_all([a, b])
        await s.flush()

        def edge(clause_row, quote, rel):
            e = KgEdge(
                country_code="SG",
                source_node_id=a.id,
                target_node_id=b.id,
                relation_type=rel,
                evidence_clause_id=clause_row.id,
                evidence_quote=quote,
                extractor_model="t",
                review_status="stale",
            )
            s.add(e)
            return e

        back = edge(old, "The Organisation shall notify the Commission within 3 days", "REQUIRES")
        gone = edge(old2, "A sentence that is nowhere in the new reading at all", "GOVERNS")
        await s.flush()
        out = await relocate_edges_by_quote(s, "SG")
        assert out["edges_relocated"] == 1 and out["still_stale"] == 1
        await s.refresh(back)
        await s.refresh(gone)
        assert back.evidence_clause_id == new.id and back.review_status == "pending_review"
        assert gone.review_status == "stale"


async def test_a_structural_node_pointing_at_an_old_version_loses_the_pointer_not_its_life(
    session_factory,
):
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.graph_validation import stale_unanchored_nodes
    from app.knowledge.models import KgNode
    from app.sources.models import SourceSite

    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="X",
            language="en",
            document_type="LAW",
            entry_url="https://e.example/st",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="SG",
            title="S",
            document_type="LAW",
            language="en",
            source_url="https://e.example/st",
        )
        s.add(doc)
        await s.flush()
        vs = []
        for no in (1, 2):
            v = DocumentVersion(
                document_id=doc.id,
                version_no=no,
                content_hash=str(no) * 64,
                archive_path="a",
                mime_type="application/pdf",
                size_bytes=1,
                final_url="https://e.example/st",
                state="parsed",
            )
            s.add(v)
            await s.flush()
            vs.append(v)
        old = Clause(
            version_id=vs[0].id,
            ordinal=1,
            language="en",
            original_text="Old.",
            text_hash=_h("Old."),
        )
        s.add(old)
        await s.flush()

        def node(name, ntype):
            n = KgNode(
                country_code="SG",
                node_type=ntype,
                node_key=name,
                name_zh=name,
                evidence_clause_id=old.id,
                review_status="approved",
            )
            s.add(n)
            return n

        law, duty = node("某法", "INSTRUMENT"), node("某义务", "OBLIGATION")
        await s.flush()
        assert await stale_unanchored_nodes(s, "SG") == 1  # 只有义务被停用
        await s.refresh(law)
        await s.refresh(duty)
        assert law.review_status == "approved" and law.evidence_clause_id is None
        assert duty.review_status == "stale"

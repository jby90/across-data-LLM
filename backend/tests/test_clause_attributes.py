from app.knowledge.clause_attributes import verify

CLAUSE = (
    "Article 12. A controller who transfers personal data outside the country must obtain the "
    "prior approval of the Commission. Records shall be kept for 3 years. The fine is up to S$1,000,000."
)


def test_only_quoted_attributes_are_kept_and_figures_must_be_in_the_quote():
    got = verify(
        [
            {
                "attribute": "authorization_requirement",
                "value": "须事先取得委员会批准",
                "quote": "must obtain the prior approval of the Commission",
            },
            {
                "attribute": "retention_period",
                "value": "保存 3 年",
                "quote": "Records shall be kept for 3 years",
            },
            {
                "attribute": "penalty_amount",
                "value": "罚款",
                "quote": "The fine is up to",
            },  # 无数字
            {
                "attribute": "data_scope",
                "value": "个人数据",
                "quote": "personal data of every kind",
            },  # 非原文
            {"attribute": "made_up", "value": "x", "quote": "must obtain the prior approval"},
        ],
        CLAUSE,
    )
    assert [g["attribute"] for g in got] == ["authorization_requirement", "retention_period"]


def test_duplicates_and_short_quotes_are_dropped():
    item = {"attribute": "data_scope", "value": "个人数据", "quote": "personal data"}
    assert len(verify([item, item], CLAUSE)) == 1
    assert verify([{**item, "quote": "data"}], CLAUSE) == []


def test_an_amount_or_period_written_in_words_counts_as_a_figure():
    fr = "Est puni d'une amende de mille dinars quiconque contrevient à l'article 39."
    ar = "يعاقب بالحبس من ستة أشهر إلى ثلاث سنوات وبالغرامة من ثلاثة ملايين إلى مئتي مليون ليرة لبنانية"
    for text, quote in ((fr, "une amende de mille dinars"), (ar, ar[:60])):
        got = verify([{"attribute": "penalty_amount", "value": "罚款", "quote": quote}], text)
        assert got and got[0]["attribute"] == "penalty_amount"
    # 没有任何数字或数字词的“罚款”仍然不收
    assert (
        verify(
            [{"attribute": "penalty_amount", "value": "罚款", "quote": "The fine is imposed by"}],
            "The fine is imposed by the Commission.",
        )
        == []
    )


async def test_obligations_are_tagged_once_and_nothing_is_removed(session_factory, monkeypatch):
    from sqlalchemy import select

    from app.analysis.llm import LlmResult
    from app.knowledge import clause_attributes as ca
    from app.knowledge.models import KgNode

    from .review_s1_s2_contracts import make_document, make_edge

    async with session_factory() as session:
        _, _, clauses = await make_document(
            session, ["The organisation must keep records for two years."]
        )
        _, source, target = await make_edge(session, clauses[0])
        ids = [source.id, target.id]
        for node in (source, target):
            node.node_type, node.evidence_clause_id = "OBLIGATION", clauses[0].id
        await session.commit()

    calls = []

    async def fake_llm(messages, **kwargs):
        calls.append(messages)
        return LlmResult(
            data={"class": "eligibility", "reason": "条件"},
            prompt_tokens=1,
            completion_tokens=1,
            duration_ms=1,
            model="fake",
        )

    monkeypatch.setattr(ca, "call_llm_json", fake_llm)
    first = await ca.classify_obligations(session_factory)
    assert first["nodes"] == 2 and first["eligibility"] == 2
    again = await ca.classify_obligations(session_factory)
    assert again["nodes"] == 0 and len(calls) == 2  # 已打标签的不再调用模型
    async with session_factory() as session:
        nodes = (await session.scalars(select(KgNode))).all()
    assert len(nodes) >= 2 and all(
        (n.attributes or {}).get("node_class") for n in nodes if n.id in ids
    )


def test_party_and_condition_are_kept_only_with_a_quote_found_in_the_clause():
    from app.knowledge.clause_attributes import verify_party

    got = verify_party(
        [
            {
                "attribute": "obligated_party",
                "value": "转移数据的控制者",
                "quote": "A controller who transfers personal data outside the country",
            },
            {
                "attribute": "applicable_condition",
                "value": "向境外传输时",
                "quote": "transfers personal data outside the country",
            },
            {
                "attribute": "applicable_condition",  # 重复项只留第一个
                "value": "另一种说法",
                "quote": "must obtain the prior approval of the Commission",
            },
            {"attribute": "obligated_party", "value": "x", "quote": "The Minister of Health"},
            {"attribute": "data_scope", "value": "个人数据", "quote": "personal data"},
            {
                "attribute": "obligated_party",
                "value": "",
                "quote": "must obtain the prior approval",
            },
        ],
        CLAUSE,
    )
    assert [g["attribute"] for g in got] == ["obligated_party", "applicable_condition"]
    assert got[1]["quote"] == "transfers personal data outside the country"


async def test_extract_party_distinguishes_nothing_stated_from_model_unavailable(monkeypatch):
    from types import SimpleNamespace

    from app.analysis.llm import LlmUnavailableError
    from app.knowledge import clause_attributes as ca

    async def silent(messages, **kwargs):
        return SimpleNamespace(data={"attributes": []})

    monkeypatch.setattr(ca, "call_llm_json", silent)
    assert await ca.extract_party("某义务", CLAUSE, "SG") == []  # 条文没写：空列表

    async def down(messages, **kwargs):
        raise LlmUnavailableError("503")

    monkeypatch.setattr(ca, "call_llm_json", down)
    assert await ca.extract_party("某义务", CLAUSE, "SG") is None  # 模型不可用：None，不当成“没写”


def test_a_long_clause_is_read_around_the_phrase_the_node_came_from():
    from app.knowledge.clause_attributes import WINDOW, clause_window

    filler = "Other provisions apply to licensees in general. " * 400
    clause = filler + "The   controller must notify the Authority within 72 hours. " + filler
    assert len(clause) > 3 * WINDOW
    window = clause_window(clause, "controller must notify the Authority")
    assert len(window) <= WINDOW and "must notify the Authority within 72 hours" in window
    # 找不到短语或没有短语：给开头（与此前行为一致）；短条款原样
    assert clause_window(clause, "a phrase that is not there") == clause[:WINDOW]
    assert clause_window(clause, None) == clause[:WINDOW]
    assert clause_window("short clause", "short") == "short clause"

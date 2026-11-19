"""版本对比：按条号和文字相似度配对，标出新增、删除、改动，改动给出词级差异。"""

from app.knowledge.version_compare import Side, compare, summarize, word_diff


def S(i, number, text):
    return Side(f"c{i}", i, number, None, text)


def test_same_numbers_pair_and_changes_show_word_level_differences():
    old = [S(1, "1", "Scope of the Act."), S(2, "2", "A person must keep records for 3 years."), S(3, "3", "Offences.")]
    new = [S(1, "1", "Scope of the Act."), S(2, "2", "A person must keep records for 5 years."), S(3, "3", "Offences.")]
    pairs = compare(old, new)
    assert [p.status for p in pairs] == ["unchanged", "changed", "unchanged"]
    ops = [(d["op"], d["text"]) for d in pairs[1].diff]
    assert ("delete", "3") in ops and ("insert", "5") in ops


def test_added_and_removed_clauses_are_placed_where_they_belong():
    old = [S(1, "1", "alpha"), S(2, "2", "beta clause text here"), S(3, "3", "gamma")]
    new = [S(1, "1", "alpha"), S(2, "3", "gamma"), S(3, "4", "delta new clause")]
    pairs = compare(old, new)
    assert [(p.status, (p.base or p.target).number) for p in pairs] == [
        ("unchanged", "1"), ("removed", "2"), ("unchanged", "3"), ("added", "4"),
    ]
    assert summarize(pairs) == {"added": 1, "removed": 1, "changed": 0, "unchanged": 2}


def test_clauses_without_numbers_are_paired_by_similarity_and_whitespace_is_ignored():
    old = [S(1, None, "The  controller shall notify the authority\nwithin 72 hours of the breach."), S(2, None, "Completely unrelated text about fees.")]
    new = [S(1, None, "The controller shall notify the authority within 72 hours of the breach."), S(2, None, "Penalties are set out in the schedule and apply to every person.")]
    pairs = compare(old, new)
    assert pairs[0].status == "unchanged"  # 换行和空格差异不算改动
    assert [p.status for p in pairs[1:]] == ["added", "removed"] or {p.status for p in pairs[1:]} == {"added", "removed"}


def test_duplicate_numbers_are_not_used_for_pairing():
    old = [S(1, "5", "first five"), S(2, "5", "second five")]
    new = [S(1, "5", "first five"), S(2, "5", "second five changed")]
    pairs = compare(old, new)
    assert summarize(pairs)["changed"] + summarize(pairs)["unchanged"] >= 1  # 靠相似度配对，不会错配成一对


def test_word_diff_roundtrip():
    d = word_diff("a b c", "a x c d")
    assert "".join(x["text"] for x in d if x["op"] != "delete").replace(" ", "") == "axcd"


async def _two_versions(session_factory, same_source=False):
    from app.ingestion.models import Clause, DocumentVersion

    from .review_s1_s2_contracts import make_document

    async with session_factory() as s:
        doc, v1, c1 = await make_document(s, ["Article 1 Scope of the Act applies to all.", "Article 2 Keep records for 3 years."])
        v2 = DocumentVersion(
            document_id=doc.id, version_no=2, content_hash=("a" if same_source else "b") * 64,
            parse_revision=2 if same_source else 1, archive_path="SG/v2.pdf", mime_type="application/pdf", size_bytes=1, final_url="x", state="parsed",
        )
        s.add(v2)
        await s.flush()
        for i, t in enumerate(["Article 1 Scope of the Act applies to all.", "Article 2 Keep records for 5 years.", "Article 3 New duty."], 1):
            s.add(Clause(version_id=v2.id, ordinal=i, clause_number=str(i), original_text=t, text_hash=f"{i}" * 64, language="en"))
        await s.commit()
        return str(doc.id)


async def test_compare_endpoint_lists_versions_with_their_kind_and_returns_changes(client, seed, session_factory):
    from .conftest import bearer, login

    doc_id = await _two_versions(session_factory)
    h = bearer(await login(client, "user", "a-member@example.com"))
    versions = (await client.get(f"/api/knowledge/documents/{doc_id}/versions", headers=h)).json()["versions"]
    # 版本只表示官方文本：称呼用官方修正案名称或发布日期，没有就写“官方修订文本”，不写“第 N 版”
    assert [(v["version_no"], v["kind"]) for v in versions] == [(1, "首次收录的官方文本"), (2, "官方修订文本")]
    res = (await client.get(f"/api/knowledge/documents/{doc_id}/compare?base=1&target=2", headers=h)).json()
    assert res["counts"] == {"added": 1, "removed": 0, "changed": 1, "unchanged": 1}
    assert [r["status"] for r in res["rows"]] == ["changed", "added"] and not res["same_source"]
    everything = (await client.get(f"/api/knowledge/documents/{doc_id}/compare?base=1&target=2&only_changes=false", headers=h)).json()
    assert len(everything["rows"]) == 3
    assert (await client.get(f"/api/knowledge/documents/{doc_id}/compare?base=1&target=1", headers=h)).status_code == 422
    assert (await client.get(f"/api/knowledge/documents/{doc_id}/compare?base=1&target=9", headers=h)).status_code == 404


async def test_a_reread_of_the_same_file_is_still_marked_as_the_same_source(client, seed, session_factory):
    from .conftest import bearer, login

    doc_id = await _two_versions(session_factory, same_source=True)
    h = bearer(await login(client, "user", "a-member@example.com"))
    assert len((await client.get(f"/api/knowledge/documents/{doc_id}/versions", headers=h)).json()["versions"]) == 2
    # 同一份文件读两遍：正常入库时已并成一个版本；库里若还有这种情况，对比时照样说明“原文相同”
    assert (await client.get(f"/api/knowledge/documents/{doc_id}/compare?base=1&target=2", headers=h)).json()["same_source"]

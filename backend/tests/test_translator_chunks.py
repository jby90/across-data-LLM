

def test_a_written_date_is_never_cut_between_two_chunks():
    from app.knowledge.numeric_integrity import dates
    from app.knowledge.translator import split_clause_into_chunks

    filler = " ".join(f"loi n° {2000 + i}-{i} du {1 + i % 27} mars {2000 + i}," for i in range(60))
    for tail in ("du 6 février 2018", "du 12 avril 2020"):
        text = filler + " " + tail + ", notamment ses articles " + " ".join(str(n) for n in range(40))
        chunks = split_clause_into_chunks(text, max_chars=700)
        assert len(chunks) > 3
        found = set().union(*(dates(c) for c in chunks))
        assert dates(text) <= found  # 原文每个完整日期，都完整地落在某一块里

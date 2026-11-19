from datetime import date

from app.ingestion.version_attributes import verify

SRC = "Article 40. This Law enters into force on 1 January 2019.\n\nAmended by Law 7 on 1 May 2021."


def test_a_value_is_kept_only_when_its_quote_is_in_the_text():
    got = verify(
        {
            "effective_date": "2019-01-01",
            "effective_quote": "This Law enters into force on 1 January 2019.",
            "amended_dates": ["2021-05-01"],
            "amended_quote": "Amended by Law 7 on 1 May 2021.",
        },
        SRC,
    )
    assert got["effective_date"] == date(2019, 1, 1) and got["amended_dates"] == [date(2021, 5, 1)]


def test_invented_quote_or_bad_date_is_dropped():
    got = verify(
        {
            "effective_date": "2019-01-01",
            "effective_quote": "This Law enters into force on the first day of 2019 exactly.",
            "amended_dates": ["2021-13-45", "not a date"],
            "amended_quote": "Amended by Law 7 on 1 May 2021.",
            "repealed": True,
            "repealed_quote": "This Law is repealed.",
        },
        SRC,
    )
    assert got == {"effective_date": None, "amended_dates": [], "repealed": False}


def test_a_quote_that_does_not_carry_the_date_does_not_support_it():
    src = "Décret n° 2-13-881 du 28 rabii I 1436 (20 janvier 2015) modifiant le décret."
    wrong = verify(
        {
            "effective_date": "2014-01-03",
            "effective_quote": "Décret n° 2-13-881 du 28 rabii I 1436 (20 janvier 2015) modifiant",
        },
        src,
    )
    right = verify(
        {
            "effective_date": "2015-01-20",
            "effective_quote": "Décret n° 2-13-881 du 28 rabii I 1436 (20 janvier 2015) modifiant",
        },
        src,
    )
    assert wrong["effective_date"] is None and right["effective_date"] == date(2015, 1, 20)


def test_arabic_indic_digits_in_the_quote_count():
    src = "يعمل بهذا القانون اعتبارا من ١ كانون الثاني ٢٠١٩ ويلغى كل نص مخالف."
    got = verify({"effective_date": "2019-01-01", "effective_quote": src}, src)
    assert got["effective_date"] == date(2019, 1, 1)


async def test_fill_versions_writes_only_what_is_quoted_and_never_overwrites(
    session_factory, monkeypatch
):
    from sqlalchemy import select

    from app.ingestion import version_attributes as va
    from app.ingestion.models import DocumentVersion

    from .review_s1_s2_contracts import make_document

    async with session_factory() as session:
        _, version, _ = await make_document(session, ["Article 1.", "Article 2 enters into force."])
        version.effective_date = date(2020, 6, 1)  # 已有值：不得被覆盖
        await session.commit()

    async def fake_extract(first, last):
        return {
            "effective_date": date(2019, 1, 1),
            "amended_dates": [date(2021, 5, 1)],
            "repealed": False,
        }

    monkeypatch.setattr(va, "extract", fake_extract)
    stats = await va.fill_versions(session_factory)
    assert stats["effective_filled"] == 0 and stats["amended_filled"] == 1
    async with session_factory() as session:
        v = (await session.scalars(select(DocumentVersion))).one()
    assert v.effective_date == date(2020, 6, 1) and v.amended_dates == [date(2021, 5, 1)]
    assert (await va.fill_versions(session_factory, country="SG"))["documents"] == 1
    assert (await va.fill_versions(session_factory, country="ZZ"))["documents"] == 0

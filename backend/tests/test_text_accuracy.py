from app.ingestion.text_accuracy import compare, words_of


def test_identical_reading_is_fully_accurate():
    r = compare("المادة 5 يحظر النقل", "المادة 5 يحظر النقل")
    assert r["accuracy"] == 1.0 and r["missed"] == r["misread"] == r["extra"] == 0


def test_a_different_way_of_writing_the_same_arabic_letters_is_not_an_error():
    assert compare("أحكام الإنتاج", "احكام الانتاج")["accuracy"] == 1.0
    assert words_of("ٱلْقَانُون") == words_of("القانون")


def test_missed_misread_and_extra_words_are_told_apart():
    truth = "the controller shall delete the data within 30 days"
    r = compare("the controller shall erase the data within days again", truth)
    assert (
        r["misread"] == 1 and r["missed"] == 1 and r["extra"] == 1
    )  # delete→erase, 30 缺, again 多
    assert r["number_errors"] == 1 and r["words"] == 9 and r["right"] == 7


def test_a_misread_number_is_counted_under_number_errors():
    r = compare("article 15 applies from 2021", "article 16 applies from 2021")
    assert r["misread"] == 1 and r["number_errors"] == 1 and r["accuracy"] == 0.8


def test_empty_truth_has_no_accuracy():
    assert compare("anything", "")["accuracy"] is None


def test_unordered_comparison_does_not_punish_a_table_read_in_another_order():
    from app.ingestion.text_accuracy import compare, compare_unordered

    truth = "الفئة صفر الفئة 1 الفئة 2 الفئة 3 الخلوية GSM"
    reading = "الفئة 3 الفئة 2 الفئة 1 الفئة صفر الخلوية GSM"
    assert compare_unordered(reading, truth)["accuracy"] == 1.0
    assert compare(reading, truth)["accuracy"] < 0.8


def test_unordered_comparison_still_counts_missing_and_wrong_words_and_figures():
    from app.ingestion.text_accuracy import compare_unordered

    r = compare_unordered("طلب الموافقة 10,000,000", "طلب الموافقة 15,000,000 النموذج")
    assert r["missed"] == 2 and r["number_errors"] == 1 and r["extra"] == 1

"""走安全评估时项目页顶部的两个期限：申报截止日（自评估完成日起 3 个月）、评估结果到期日（出具日起 3 年）；
没填日期时提示去填；拿到评估结果后不再计申报截止；延长只写文字不算日期；其他路径不显示。"""

from datetime import date

from app.analysis import cn_route
from app.core import dates

from .conftest import bearer, login
from .test_doc_projects import BASE, _make

TODAY = date(2026, 11, 10)


def test_periods_end_on_the_corresponding_day_or_the_last_day_of_the_month():
    assert dates.add_months(date(2026, 11, 10), 3) == date(2027, 2, 10)
    assert dates.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)  # 没有对应日：月末
    assert dates.add_months(date(2028, 2, 29), 36) == date(2031, 2, 28)
    assert dates.expiry_state(None) == "待填有效期"
    assert [dates.expiry_state(d) for d in (-1, 0, 90, 91)] == ["已过期", "即将到期", "即将到期", "有效"]


def test_month_end_and_leap_day():
    # 1 月 31 日加 1 个月：2 月没有 31 日，取 2 月最后一天（平年 28 日、闰年 29 日）
    assert dates.add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert dates.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert dates.add_months(date(2026, 10, 31), 3) == date(2027, 1, 31)  # 有对应日：照取
    assert dates.add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)  # 跨年
    # 闰年 2 月 29 日加 36 个月：3 年后不是闰年，取 2 月 28 日；加 48 个月又是闰年，取 29 日
    assert dates.add_months(date(2028, 2, 29), 36) == date(2031, 2, 28)
    assert dates.add_months(date(2028, 2, 29), 48) == date(2032, 2, 29)
    assert dates.add_months(date(2027, 2, 28), 36) == date(2030, 2, 28)
    assert dates.parse("2026-02-30") is None and dates.parse("") is None and dates.parse("2026-11-10") == date(2026, 11, 10)
    assert dates.days_left(date(2026, 11, 10), date(2026, 11, 10)) == 0


def test_no_dates_leaves_both_periods_open():
    v = cn_route.validity("", "", TODAY)
    assert v["filing"] is None and v["result"] is None
    assert v["extend"] == "有效期届满前 60 个工作日内可申请延长"
    assert [b["article"] for b in v["basis"]] == ["附件 4 注（二）", "第九条", "三、（一）"]


def test_filing_is_due_three_months_after_the_self_assessment():
    v = cn_route.validity("2026-09-01", "", TODAY)
    assert v["filing"] == {"date": "2026-12-01", "days_left": 21, "state": "即将到期"}
    assert cn_route.validity("2026-07-01", "", TODAY)["filing"]["days_left"] < 0  # 超过 3 个月


def test_once_the_result_is_issued_it_runs_three_years_and_filing_no_longer_counts():
    v = cn_route.validity("2026-09-01", "2026-10-20", TODAY)
    assert v["filing"] is None
    assert v["result"] == {"date": "2029-10-20", "days_left": (date(2029, 10, 20) - TODAY).days, "state": "有效"}


def test_not_a_date_is_ignored():
    v = cn_route.validity("下周", "2026-13-01", TODAY)
    assert v["assessed_on"] == "" and v["issued_on"] == "" and v["filing"] is None and v["result"] is None


SA = {"is_cii": "否", "important_data": "否", "sensitive_pi": "否", "data_volume": "约 150 万人"}


async def test_the_project_page_shows_the_periods_only_on_the_security_assessment_route(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="埃及", scenario_text="车辆运行数据回传海外研发中心")
    pid = p["id"]
    p = (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": SA})).json()
    assert p["cn_route"]["route"] == "security_assessment"
    v = p["cn_route"]["validity"]
    assert v["filing"] is None and v["result"] is None  # 没填日期：页面提示去填
    today = date.today()
    assessed = dates.add_months(today, -1).isoformat()
    r = await client.patch(f"{BASE}/{pid}", headers=auth, json={"self_assessed_on": assessed})
    v = r.json()["cn_route"]["validity"]
    assert v["assessed_on"] == assessed and v["filing"]["date"] == dates.add_months(dates.parse(assessed), 3).isoformat()
    assert all(d["state"] for d in r.json()["documents"])  # 日期是项目信息，不进文件、不改状态
    assert not any(q["key"] in ("self_assessed_on", "result_issued_on") for q in r.json()["questions"])
    # 出具日早于完成日、晚于今天、不是日期：拒收
    bad = dates.add_months(dates.parse(assessed), -1).isoformat()
    assert (await client.patch(f"{BASE}/{pid}", headers=auth, json={"result_issued_on": bad})).status_code == 422
    assert (await client.patch(f"{BASE}/{pid}", headers=auth, json={"result_issued_on": "2999-01-01"})).status_code == 422
    assert (await client.patch(f"{BASE}/{pid}", headers=auth, json={"result_issued_on": "明天"})).status_code == 422
    r = await client.patch(f"{BASE}/{pid}", headers=auth, json={"result_issued_on": today.isoformat()})
    v = r.json()["cn_route"]["validity"]
    assert v["filing"] is None and v["result"]["date"] == dates.add_months(today, 36).isoformat()
    # 改成标准合同路径：不显示这两个期限
    p = (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {**SA, "data_volume": "约 20 万人"}})).json()
    assert p["cn_route"]["route"] == "standard_contract" and p["cn_route"]["validity"] is None
    # 清空日期
    r = await client.patch(f"{BASE}/{pid}", headers=auth, json={"self_assessed_on": "", "result_issued_on": ""})
    assert r.status_code == 200


def test_the_approval_ledger_uses_the_same_expiry_words():
    from types import SimpleNamespace

    from app.admission.router import approval_out

    soon = dates.add_months(date.today(), 1).isoformat()
    later = dates.add_months(date.today(), 12).isoformat()
    past = dates.add_months(date.today(), -1).isoformat()
    states = [approval_out(SimpleNamespace(approval={"number": "A1", "expires_on": d}))["state"] for d in (soon, later, past, "")]
    assert states == ["\u5373\u5c06\u5230\u671f", "\u6709\u6548", "\u5df2\u8fc7\u671f", "\u5f85\u586b\u6709\u6548\u671f"]


def test_overdue_words_match_the_ledger():
    from app.analysis.project_guide import period_text

    over = cn_route.validity("2026-07-01", "", TODAY)["filing"]
    assert period_text(over, "\u5df2\u8d85\u8fc7").endswith("\u5df2\u8d85\u8fc7 40 \u5929")  # 申报截止：已超过
    gone = cn_route.validity("", "2023-11-01", TODAY)["result"]
    assert gone["state"] == "\u5df2\u8fc7\u671f" and period_text(gone).endswith("\u5df2\u8fc7\u671f 9 \u5929")  # 到期日：已过期（与批文台账同词）

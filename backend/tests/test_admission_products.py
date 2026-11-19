"""第 12 条：准入入口按产品类别筛选（乘用车、商用车、车载通信模块），事项卡列依据法规。"""

from sqlalchemy import select

from app.admission import products as P
from app.admission.models import AdmissionEntry

from .conftest import bearer, login


def test_device_items_are_the_module_and_not_a_vehicle_type():
    assert P.products_of("telecom_approval", "电信设备型式批准认证") == ["module"]
    assert P.products_of("telecom_approval", "消费者物联网网络安全标签申请") == ["module"]
    assert P.products_of("telecom_approval", "无线电设施与终端设备批准") == ["module"]
    # 审批对象是设备、模块、加密手段的都算设备类（不只型式批准）
    assert P.products_of("telecom_approval", "通信设备安装使用许可") == ["module"]
    assert P.products_of("telecom_approval", "UHF/VHF无线设备年度许可核定") == ["module"]
    assert P.products_of("other", "硬件安全模块批准") == ["module"]
    assert P.products_of("other", "加密手段技术认证核准") == ["module"]
    # 经营网络或服务、用频许可是给运营方的：各类都要
    assert P.products_of("telecom_approval", "电信网络与服务经营许可") == P.EVERY
    assert P.products_of("telecom_approval", "无线电频率使用许可证") == P.EVERY
    assert P.products_of("telecom_approval", "物联网服务提供许可") == P.EVERY
    assert P.products_of("data_filing", "生物识别装置安装授权") == P.EVERY  # 数据处理授权：不按设备分
    assert not P.fits("passenger", "telecom_approval", "电信设备型式批准认证")


def test_vehicle_items_follow_the_class_their_name_names():
    assert P.products_of("vehicle_approval", "车辆型式认证申请办理") == P.VEHICLES
    assert P.products_of("vehicle_approval", "N3类货车型式批准") == ["commercial"]
    assert P.products_of("vehicle_approval", "M1类乘用车型式批准") == ["passenger"]
    assert P.products_of("av_testing", "自动驾驶车辆道路使用授权") == P.VEHICLES
    assert P.products_of("data_filing", "个人数据跨境传输许可") == P.EVERY
    assert P.products_of("other", "") == P.EVERY  # 分不清的：各类车辆
    assert P.label(P.EVERY) == "各类车辆" and P.label(P.VEHICLES) == "乘用车、商用车"
    assert P.fits("", "telecom_approval", "电信设备型式批准认证")  # 不选：全部


async def test_the_plan_and_the_library_filter_by_product(client, seed, session_factory):
    async with session_factory() as s:
        s.add_all([
            AdmissionEntry(country_code="SG", name_zh="电信设备注册", category="telecom_approval", authority_zh="IMDA", summary_zh="销售前注册。", review_status="approved"),
            AdmissionEntry(country_code="SG", name_zh="车辆注册批准", category="vehicle_approval", authority_zh="LTA", summary_zh="注册前批准。", review_status="approved"),
            AdmissionEntry(country_code="SG", name_zh="个人数据跨境传输许可", category="data_filing", authority_zh="PDPC", summary_zh="传输前许可。", review_status="approved"),
        ])
        await s.flush()
        # 用户端只展示资料齐的事项（有依据法规、有办理环节）
        from app.admission.models import AdmissionStep

        for e in (await s.scalars(select(AdmissionEntry))).all():
            e.profile, e.edited = {"cited_laws": ["测试法规"]}, ["profile:cited_laws"]
            s.add(AdmissionStep(entry_id=e.id, seq=1, title_zh="提出申请", detail_zh="", materials=[], evidence_quote=""))
        await s.commit()
    auth = bearer(await login(client, "user", "a-member@example.com"))
    names = lambda rows: {e["name_zh"] for e in rows}  # noqa: E731
    lib = (await client.get("/api/admission/entries", headers=auth, params={"country": "SG", "product": "passenger"})).json()
    assert "电信设备注册" not in names(lib) and {"车辆注册批准", "个人数据跨境传输许可"} <= names(lib)
    mod = (await client.get("/api/admission/entries", headers=auth, params={"country": "SG", "product": "module"})).json()
    assert "车辆注册批准" not in names(mod) and {"电信设备注册", "个人数据跨境传输许可"} <= names(mod)
    assert all("products_zh" in e and "basis" in e for e in lib)
    plan = (await client.get("/api/admission/plan", headers=auth, params={"country": "SG", "product": "module"})).json()
    assert plan["product"] == "module" and "车辆注册批准" not in names(plan["needed"] + plan["maybe"])
    everything = (await client.get("/api/admission/plan", headers=auth, params={"country": "SG"})).json()
    assert names(everything["needed"] + everything["maybe"]) >= names(plan["needed"] + plan["maybe"])

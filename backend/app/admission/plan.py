# ruff: noqa: E501
"""Admission as the enterprise's aid: which items a business needs in a country, and a check of the
material the enterprise filled before it goes to the authority.

Which items: every vehicle sold or tested needs the vehicle approvals, the approvals of its
in-vehicle radio and telecom equipment, and — since it processes personal data there — the data
processing declaration or licence (and the transfer authorisation when the data leaves the
country). Autonomous-driving tests add the testing authorisations. Other items (e-signature,
video surveillance…) are listed apart as “may apply”. The rule is by the item's category, the same
for every country.

The check: the platform cannot see the authority, so “审核问题” are what the platform itself finds
in the filled material — required information still missing, official forms not filled or with
required fields empty, the same fact written differently in two places, and an application
written before the information last changed. The originals the enterprise brings itself are only
listed (the platform does not collect them), so they are not checked. Each run
is recorded: new findings are added, findings no longer present are closed.
"""

from __future__ import annotations

import hashlib
import re

# 合规评估的“车辆数据主要用途”（doc_projects.SHORT_FUNCTION 的键）
FUNCTIONS = {
    "autonomous_trial": "自动驾驶测试",
    "connected_vehicle_cloud": "车联网上云",
    "ota_diagnostics": "远程诊断与OTA",
    "cockpit_biometrics": "座舱与生物识别",
    "hd_mapping": "高精地图与测绘",
    "fleet_mobility": "车队与出行服务",
    "sales_service": "销售与售后客户数据",
    "insurance_ubi": "保险与驾驶行为",
    "charging_energy": "充电与能源服务",
}
ALWAYS = ("vehicle_approval", "telecom_approval", "data_filing")
ONLY_FOR = {"av_testing": ("autonomous_trial",)}


# 平台的四类业务都会把车辆数据传回中国：数据出境授权、一般的个人数据处理申报/许可，四类业务都要办
ALWAYS_BY_NAME = re.compile(r"跨境|出境|向境外|传输许可|传输授权|(个人数据处理(事先)?(申报|许可|登记))")
SENSITIVE_ONLY = re.compile(r"生物|敏感|视频|监控")


# 车辆上市（型式认证、登记、进口放行）、车载无线与通信模块入网：任何业务的车辆都要办（改装、过户、方向盘变更这类特殊情形除外）
EVERY_VEHICLE = re.compile(r"(车辆|机动车|汽车).{0,8}(认证|型式|核准|批准|上路|登记|注册)|型式(批准|认证|核准)|上路核准|(车辆|乘用车|汽车|商用车).{0,8}进口.{0,4}(放行|条件|许可)")
EVERY_EQUIPMENT = re.compile(r"(电信|通信|无线电?|终端).{0,8}设备.{0,10}(型式|型号|注册|批准|认证|认可|核准)|设备注册|型式(批准|认证|核准|认可)|设施与终端设备批准")
SPECIAL_CASE = re.compile(r"改装|变更|更改|转让|过户|方向盘|破封|特殊计划|豁免|自用|临时进口|进口制造|修改|重新认证|年度|频率|号牌")


def applies(category: str, function: str, functions: list[str] | None = None, name: str = "") -> str:
    """"needed" / "maybe" / "" for an item and the business ``function``. The item's own tags
    (which businesses need it) decide when present; otherwise the rule by category."""
    if category in ("vehicle_approval", "telecom_approval") and not SPECIAL_CASE.search(name) and (
        (category == "vehicle_approval" and EVERY_VEHICLE.search(name)) or (category == "telecom_approval" and EVERY_EQUIPMENT.search(name))
    ):
        return "needed"
    if category == "data_filing" and ALWAYS_BY_NAME.search(name) and not SENSITIVE_ONLY.search(name.replace("（含敏感数据）", "")):
        return "needed"
    if functions and function:
        if "all" in functions or function in functions:
            return "needed"
        return "maybe" if "none" not in functions else ""
    if functions and "none" in functions:
        return "maybe"
    if category in ALWAYS:
        return "needed"
    if category in ONLY_FOR:
        return "needed" if function in ONLY_FOR[category] or not function else ""
    return "maybe"


def finding_key(target: str, text: str) -> str:
    return target + ":" + hashlib.sha1(re.sub(r"\d+", "#", text).encode("utf-8")).hexdigest()[:12]


def _same(a: str, b: str) -> bool:
    norm = lambda s: re.sub(r"[\s（）()，,。.·•\-_]", "", (s or "").lower())  # noqa: E731
    return norm(a) == norm(b) or norm(a) in norm(b) or norm(b) in norm(a)


def findings(*, missing: list[tuple[str, str]], forms: list[dict],
             drafts: list[dict], facts_changed_at, company: str, vehicle: str) -> list[dict]:
    """What is wrong or missing in the material, each {target, text, blocking}. ``forms``: one per
    official form of the item: {code, name, filled: bool, missing: [labels], values: {fact: value}}."""
    out: list[dict] = []
    for key, label in missing:
        out.append({"target": f"info:{key}", "text": f"还缺信息：{label}", "blocking": True})
    for f in forms:
        if not f["filled"]:
            out.append({"target": f"form:{f['code']}", "text": f"官方表格《{f['name']}》还没有填写", "blocking": True})
            continue
        if f["missing"]:
            names = "、".join(f["missing"][:6]) + (" 等" if len(f["missing"]) > 6 else "")
            out.append({"target": f"form:{f['code']}", "text": f"《{f['name']}》还有 {len(f['missing'])} 项必填未填：{names}", "blocking": True})
        for fact, mine, what in (("company_name", company, "申请主体名称"), ("vehicle_model", vehicle, "车型")):
            theirs = (f.get("values") or {}).get(fact, "")
            if mine and theirs and not _same(mine, theirs):
                out.append({"target": f"form:{f['code']}", "text": f"《{f['name']}》里的{what}“{theirs}”与项目里的“{mine}”不一致", "blocking": True})
    if not drafts:
        out.append({"target": "draft", "text": "还没有生成申请材料", "blocking": True})
    elif facts_changed_at and drafts[0]["created_at"] < facts_changed_at:
        out.append({"target": "draft", "text": "信息在生成申请材料之后改过，申请材料需要重新生成", "blocking": True})
    return out

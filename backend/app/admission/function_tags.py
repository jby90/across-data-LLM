# ruff: noqa: E501
"""Tag each admission item with the businesses it applies to.

The four businesses differ in what they do in the country: autonomous testing puts test vehicles
on public roads with cameras and remote monitoring; the connected-vehicle cloud sends telematics of
sold vehicles to a cloud abroad; remote diagnostics/OTA reaches into vehicles from abroad and ships
signed software; cockpit and biometrics process voice, faces and driver-state data. An item such as
“biometric device authorisation” concerns only the last; vehicle type approval concerns every
vehicle put on the market. The model reads each item (name, summary, authority) and says which
businesses need it, or “all”; the result is kept on the item and the admission plan uses it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission.models import AdmissionEntry
from app.admission.plan import FUNCTIONS
from app.analysis.llm import LlmUnavailableError, call_llm_json

SYSTEM = (
    "你是汽车出海合规顾问。下面是某国的准入事项（向政府主管机构申请的许可、认证、申报）。中国车企在该国开展的业务分以下几类：\n"
    "autonomous_trial = 自动驾驶测试（测试车上公共道路，车外摄像头、远程监控）；\n"
    "connected_vehicle_cloud = 车联网上云（已售车辆的遥测与位置数据同步到境外云）；\n"
    "ota_diagnostics = 远程诊断与 OTA（从境外远程诊断车辆、分发签名的软件升级包）；\n"
    "cockpit_biometrics = 座舱与生物识别（语音交互、人脸、驾驶员状态识别）；\n"
    "hd_mapping = 高精地图与测绘（车载激光雷达、摄像头采集道路与地理信息，涉及测绘资质与地理数据管制）；\n"
    "fleet_mobility = 车队与出行服务（网约车、物流、租车车队的车辆、司机与乘客数据，常需运营与交通主管部门许可）；\n"
    "sales_service = 销售与售后客户数据（经销商、售后与客户关系管理中的车主个人数据）；\n"
    "insurance_ubi = 保险与驾驶行为（UBI 车险，涉及驾驶行为、位置数据与金融保险监管）；\n"
    "charging_energy = 充电与能源服务（充电桩、换电与车网互动，涉及能源与电力设备准入）；\n"

    "逐项判断开展哪些业务时需要办这一项：任何车辆上市或上路都要办的（车辆型式认可、车载无线设备认可、一般个人数据处理申报、数据出境授权等）写 all；"
    "只有某些业务涉及的，列出这些业务；这些业务都不需要的写空数组。只看事项本身，不要推测。"
    '只返回 JSON：{"items":[{"i":序号,"functions":["all"]}]}'
)


async def tag(session: AsyncSession, *, redo: bool = False) -> dict:
    import app.models_registry  # noqa: F401  模型调用要写出站审计，需全部表已登记
    q = select(AdmissionEntry).where(AdmissionEntry.review_status.in_(("pending_review", "approved")), AdmissionEntry.merged_into.is_(None))
    rows = [e for e in (await session.scalars(q)).all() if redo or not e.functions]
    by_cc: dict[str, list[AdmissionEntry]] = {}
    for e in rows:
        by_cc.setdefault(e.country_code, []).append(e)
    done = tokens = 0
    batches = [(cc, g[k : k + 12]) for cc, g in by_cc.items() for k in range(0, len(g), 12)]
    for cc, group in batches:
        listing = "\n".join(f"{i}. {e.name_zh}｜受理：{e.authority_zh or '未写'}｜{e.summary_zh}" for i, e in enumerate(group))
        try:
            res = await call_llm_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"国家代码：{cc}\n{listing}"}], max_tokens=8000, temperature=0.0
            )
        except LlmUnavailableError:
            continue
        tokens += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        for item in (res.data or {}).get("items", []) if isinstance(res.data, dict) else []:
            i = item.get("i")
            i = int(i) if isinstance(i, str) and i.isdigit() else i
            fs = item.get("functions")
            if isinstance(i, int) and 0 <= i < len(group) and isinstance(fs, list):
                clean = ["all"] if "all" in fs else [f for f in fs if f in FUNCTIONS]
                group[i].functions = clean or ["none"]
                done += 1
    await session.commit()
    return {"checked": len(rows), "tagged": done, "tokens": tokens}

"""国家页的监管机构：同一机构的不同叫法只算一个，没点名的角色不算。"""

import uuid

from app.core.rls import system_scope
from app.knowledge.models import KgNode
from app.knowledge.whitepaper_graph import _regulators, authority_resolver


async def test_one_regulator_per_body_and_no_unnamed_roles(session_factory):
    def node(zh, orig, mentions=1, **attrs):
        return KgNode(country_code="XY", node_type="REGULATOR", node_key=uuid.uuid4().hex, name_zh=zh, name_orig=orig,
                      review_status="approved", attributes={"evidence_clause_ids": [str(uuid.uuid4()) for _ in range(mentions)], **attrs})

    with system_scope("test regulators"):
        async with session_factory() as s:
            s.add_all([
                node("新加坡个人数据保护委员会 PDPC", "Personal Data Protection Commission (PDPC)", 3),
                node("个人资料保护委员会（Commission）", "Commission"),
                node("个人数据保护委员会", "the Commission", 5),
                node("新加坡陆路交通管理局", "LTA"),
                node("陆路交通管理局", "Land Transport Authority"),
                node("执法机构", "law enforcement agency"),
                node("主管机关（autorités compétentes）", "autorités compétentes"),
                node("注册官或授权人员", "Registrar or an authorised officer"),
                node("旧节点", "old", merged_into=str(uuid.uuid4())),
            ])
            await s.commit()
            names = sorted(r.name_zh for r in await _regulators(s, "XY"))
            resolve = await authority_resolver(s, "XY")
    assert names == ["新加坡个人数据保护委员会 PDPC", "新加坡陆路交通管理局"]
    # 准入事项的受理机构：法条里的简称换成正式名称；认不出的、写了几个机构的照原样
    assert resolve("委员会（Commission）") == "新加坡个人数据保护委员会 PDPC"
    assert resolve("LTA") == "新加坡陆路交通管理局"
    assert resolve("LTA / 海关") == "LTA / 海关"
    assert resolve("الهيئة（电信监管机构）") == "电信监管机构（الهيئة）"

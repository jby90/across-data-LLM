# ruff: noqa: E501
"""Data hygiene for KG node labels, Lebanon country node review_status, Egypt Art. 27 90-day period,
and malformed terminology fragment exclusion."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Restore Lebanon COUNTRY node to 'approved' + active verify_state so Cockpit (6 countries) and KG LB Country=1 match
    conn.execute(
        sa.text(
            "UPDATE kg_nodes SET review_status = 'approved', verify_state = 'auto' "
            "WHERE country_code = 'LB' AND node_type = 'COUNTRY'"
        )
    )

    # 2. Fix Egypt Executive Regulations Art. 27 OCR digit drop ('9个工作日' -> '90个工作日')
    eg_row = conn.execute(
        sa.text("SELECT id, attributes FROM kg_nodes WHERE id = 'af725a8d-3c48-4d35-9c33-1835a237f5c6'")
    ).first()
    if eg_row and isinstance(eg_row[1], dict):
        attrs = dict(eg_row[1])
        onto = dict(attrs.get("ontology") or {})
        cbc = dict(onto.get("cross_border_condition") or {})
        if "9个工作日" in str(cbc.get("value") or ""):
            cbc["value"] = str(cbc["value"]).replace("9个工作日", "90个工作日")
            onto["cross_border_condition"] = cbc
            attrs["ontology"] = onto
            conn.execute(
                sa.text("UPDATE kg_nodes SET attributes = CAST(:attrs AS jsonb) WHERE id = :id"),
                {"id": eg_row[0], "attrs": json.dumps(attrs, ensure_ascii=False)},
            )

    # 3. Replace statutory illustration placeholder letters (X/Y/Z/P/T) & bare section numbers in KG nodes
    node_renames = [
        ("d79b2195-9c20-4666-b0e1-876bb2a14f17", "交易未达成时潜在交易方销毁或返还个人数据义务"),
        ("3f12999d-1a9c-4d99-974b-2450a1dfeada", "交易终止时披露方销毁或返还个人数据义务"),
        ("3f422ed2-9af9-43d9-925d-2e51df0bcdcb", "数据可携接收组织限定用途义务"),
        ("08e35dad-5f72-4fcc-bc14-e3b683e257f9", "关键信息基础设施关联系统网络安全合规义务"),
        ("11b1361e-595f-4be5-8c8a-f18c944628f8", "特殊网络安全利益实体安全合规义务"),
        ("f08dfd4e-d45b-4adf-bad4-c798d4e2db3d", "主要基础数字基础设施服务提供者安全合规义务"),
        ("7660535b-c54e-492b-857c-dbfc2543a3f3", "个人数据更正义务及法定豁免情形"),
        ("d59b9edd-dc29-4001-a262-2deb30d3649b", "物联网网络安全标签符合性声明与实验室评估核查要求"),
        ("3ccf0850-07b8-447a-83cf-936fe0d101e7", "物联网安全标签申请不符合项整改要求"),
        ("f649b014-181a-4834-8337-4e84e6fc8022", "向非充分保护国家跨境传输个人数据的前置条件与例外"),
        ("0e200dd4-6bd4-4d76-aded-51d75db6c3c3", "车辆技术检验机构法人资格与准入条件"),
        ("70959d64-3097-45c8-bc7a-1c5f8800fdf4", "须经事先授权处理的敏感个人数据"),
        ("8bb001b7-f672-48db-b2d6-a14a2d94aae4", "道路交通管理登记信息与数据"),
    ]
    for nid, new_name in node_renames:
        conn.execute(
            sa.text("UPDATE kg_nodes SET name_zh = :name WHERE id = :id"),
            {"id": nid, "name": new_name},
        )

    # Clean X/Y/Z/P/T ontology party/condition placeholders on SG statutory illustration nodes
    onto_patches = {
        "d79b2195-9c20-4666-b0e1-876bb2a14f17": {"obligated_party": "潜在受让方组织"},
        "3f12999d-1a9c-4d99-974b-2450a1dfeada": {"obligated_party": "交易披露方组织"},
        "15178f31-8459-4949-9dbe-365ffe3e144f": {
            "obligated_party": "交易相关各方组织",
            "data_scope": "仅限为判断是否进行相关交易而必需的个人数据",
            "applicable_condition": "协议须要求接收方仅将个人数据用于或披露于与相关交易有关的目的",
        },
        "d7215592-4a7b-4abc-946a-9dbde5d5a05e": {
            "obligated_party": "交易相关各方组织",
            "applicable_condition": "披露方从原持有组织收集相关个人数据时",
        },
        "a41d5b40-ea55-4199-a30c-830391f361ec": {
            "obligated_party": "业务资产交易双方组织",
            "data_scope": "转让方组织适用个人的个人数据",
            "applicable_industry": "参与或拟参与业务资产交易的双方组织",
            "applicable_condition": "作为业务资产交易潜在受让方时",
        },
        "b6c3d64d-ab70-4564-854d-47b15079718d": {
            "obligated_party": "业务资产交易双方组织",
            "data_scope": "与业务资产交易相关的适用个人数据",
            "applicable_condition": "作为业务资产交易潜在受让方时",
        },
        "6b97899f-51e9-4c62-9321-47e0275e26c2": {
            "obligated_party": "业务资产交易双方组织",
            "data_scope": "仅限为判断是否进行相关交易而必需的个人数据",
            "applicable_condition": "达成相关业务资产交易后使用或披露交易收集的个人数据时",
        },
        "2d56b4c4-1c2f-49a1-be8a-c637ce326a71": {
            "obligated_party": "个人数据接收方组织",
            "applicable_condition": "发送方与接收方组织受具有法律约束力的合同、协议或集团规则约束",
        },
        "3f422ed2-9af9-43d9-925d-2e51df0bcdcb": {
            "data_scope": "数据可携请求所涉的用户活动数据或用户提供数据",
            "applicable_condition": "接收组织依数据可携请求从原组织接收个人数据时",
            "authorization_requirement": "数据可携请求须由个人提出，且接收组织仅限用于向该个人提供商品或服务",
        },
    }
    for nid, field_updates in onto_patches.items():
        row = conn.execute(
            sa.text("SELECT id, attributes FROM kg_nodes WHERE id = :id"),
            {"id": nid},
        ).first()
        if not row or not isinstance(row[1], dict):
            continue
        attrs = dict(row[1])
        onto = dict(attrs.get("ontology") or {})
        changed = False
        for k, val in field_updates.items():
            if isinstance(onto.get(k), dict):
                sub = dict(onto[k])
                sub["value"] = val
                onto[k] = sub
                changed = True
        if changed:
            attrs["ontology"] = onto
            conn.execute(
                sa.text("UPDATE kg_nodes SET attributes = CAST(:attrs AS jsonb) WHERE id = :id"),
                {"id": row[0], "attrs": json.dumps(attrs, ensure_ascii=False)},
            )

    # 4. Exclude malformed sentence-fragment term ('of a notifiable data breach4') from country glossary
    conn.execute(
        sa.text(
            "UPDATE legal_term_definitions SET business_close = 'not_term' "
            "WHERE id = '24ab2144-0955-4d72-88ae-dfcbb237f2b1'"
        )
    )


def downgrade():
    pass

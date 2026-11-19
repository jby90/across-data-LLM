"""Compliance document catalogue and the application-form recognition library skeleton.

Which documents a company must produce depends on the sending country and the destination
country and on what the business does. The rules below are data: one entry per document
type, with the origin/destination it applies to, an optional business condition, and an
honest statement of whether an official template exists. Matching never branches on a
country code; adding a country or a document type is adding an entry.

Principles carried over from the 10.22 meeting:
  * documents are decided by "origin + destination", not by a fixed count;
  * a destination without an official template is listed as a requirement, but the system
    does not generate a document for it (template_status != "official");
  * an unknown origin or destination yields an explicit "not covered" result, never a guess.

Entries are a skeleton: ``template_status`` is "official" only where the template has been
checked against the issuing authority's own publication. Only the Singapore LTA application
form has been checked so far; every other entry stays "unverified" until it is.
"""

from dataclasses import dataclass, field

ANY = "*"


@dataclass(frozen=True)
class DocumentType:
    document_id: str
    name_zh: str
    origin: str  # country code of the sending side, or "*"
    destination: str  # country code of the receiving side, or "*"
    # business condition: fact key -> required truthy value; empty means always
    when: dict = field(default_factory=dict)
    template_status: str = "unverified"  # official | none | unverified
    authority: str = ""
    basis: str = ""


CATALOGUE: list[DocumentType] = [
    # Outbound from China: provision of personal information abroad.
    DocumentType(
        "cn-outbound-standard-contract",
        "个人信息出境标准合同及备案材料",
        "CN",
        ANY,
        {"personal_data_transfer": True},
        template_status="unverified",
        authority="国家互联网信息办公室",
        basis="10.22 会议：中国出境参照网信办模板",
    ),
    DocumentType(
        "cn-outbound-pia-report",
        "个人信息保护影响评估报告",
        "CN",
        ANY,
        {"personal_data_transfer": True},
        template_status="unverified",
        authority="国家互联网信息办公室",
        basis="10.22 会议：中国出境参照网信办模板",
    ),
    # Inbound to the EU: GDPR contractual and assessment documents.
    DocumentType(
        "eu-inbound-dpa-contract",
        "数据处理与跨境传输合同（GDPR）",
        ANY,
        "EU",
        {"personal_data_transfer": True},
        template_status="unverified",
        authority="欧盟委员会",
        basis="10.22 会议：欧盟入境触发 GDPR 合同与 DPIA",
    ),
    DocumentType(
        "eu-inbound-dpia",
        "数据保护影响评估（DPIA）",
        ANY,
        "EU",
        {"personal_data_transfer": True},
        template_status="unverified",
        authority="欧盟成员国数据保护机构",
        basis="10.22 会议：欧盟入境触发 GDPR 合同与 DPIA",
    ),
    # Pilot countries: obligations exist in the archived texts, but no official application
    # template has been verified yet; the system lists them and does not generate them.
    DocumentType(
        "eg-pdpc-licence-application",
        "个人数据保护中心许可证/批准申请",
        ANY,
        "EG",
        {"personal_data_transfer": True},
        template_status="unverified",
        authority="埃及个人数据保护中心",
        basis="埃及个人数据保护法第 14、16、26、27 条及执行条例",
    ),
    DocumentType(
        "tn-inpdp-declaration",
        "国家个人数据保护机构处理申报/授权申请",
        ANY,
        "TN",
        {"personal_data_processing": True},
        template_status="unverified",
        authority="突尼斯国家个人数据保护机构",
        basis="突尼斯 2004 年第 63 号法第 7、15、52 条",
    ),
    DocumentType(
        "ma-cndp-declaration",
        "国家委员会处理申报/授权申请",
        ANY,
        "MA",
        {"personal_data_processing": True},
        template_status="unverified",
        authority="摩洛哥国家个人数据保护监督委员会",
        basis="摩洛哥 09-08 号法第 12–15、21 条及第 2-09-165 号法令第 23–27 条",
    ),
    DocumentType(
        "lb-ministry-declaration",
        "经济与贸易部个人数据处理申报",
        ANY,
        "LB",
        {"personal_data_processing": True},
        template_status="unverified",
        authority="黎巴嫩经济与贸易部",
        basis="黎巴嫩 2018 年第 81 号法第 95–97 条",
    ),
    DocumentType(
        "sg-av-trial-application",
        "自动驾驶车辆道路测试申请",
        ANY,
        "SG",
        {"road_trial": True},
        template_status="official",
        authority="新加坡陆路交通管理局",
        basis="Road Traffic (Autonomous Motor Vehicles) Rules 2017 第 5 条；库内已收录 LTA 申请表",
    ),
    DocumentType(
        "sg-pdpa-transfer-assurance",
        "跨境传输接收方保护保证文件",
        ANY,
        "SG",
        {"personal_data_transfer": True},
        template_status="none",
        authority="新加坡个人数据保护委员会",
        basis="PDPA 第 26 条与 2021 年条例第 10 条：须保证接收方保护水平，无官方统一模板",
    ),
]

KNOWN_COUNTRIES = {t.origin for t in CATALOGUE} | {t.destination for t in CATALOGUE}
KNOWN_COUNTRIES.discard(ANY)


def required_documents(origin: str, destination: str, facts: dict | None = None) -> dict:
    """Documents triggered for an origin/destination pair and a set of business facts.

    Returns ``covered`` (the pair is known to the catalogue), ``documents`` (triggered
    entries with whether the system may generate them) and ``not_covered`` (what could not
    be decided and why).
    """
    facts = facts or {}
    origin, destination = (origin or "").upper(), (destination or "").upper()
    not_covered = []
    for role, code in (("发出国", origin), ("目的国", destination)):
        if not code:
            not_covered.append(f"{role}未提供")
        elif code not in KNOWN_COUNTRIES:
            not_covered.append(f"{role} {code} 尚无文件目录条目，未作判定")
    documents = []
    for entry in CATALOGUE:
        if entry.origin not in (ANY, origin) or entry.destination not in (ANY, destination):
            continue
        if any(facts.get(key) != value for key, value in entry.when.items()):
            continue
        documents.append(
            {
                "document_id": entry.document_id,
                "name_zh": entry.name_zh,
                "authority": entry.authority,
                "basis": entry.basis,
                "template_status": entry.template_status,
                "generate": entry.template_status == "official",
            }
        )
    return {
        "origin": origin,
        "destination": destination,
        "covered": not not_covered,
        "documents": documents,
        "not_covered": not_covered,
    }

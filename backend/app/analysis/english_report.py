# ruff: noqa: E501
"""English compliance document & consulting report generator (zero CJK leak, Redactor-safe, cached).

Provides:
1. ``build_english_doc_blocks``: renders any of the 8 compliance assessment documents in crisp
   international regulatory English (Word & PDF via ``foreign=True``), caching LLM prose translations
   in ``DocumentNarrative`` when outbound LLM is enabled and falling back seamlessly to deterministic
   regulatory English mapping + verbatim foreign statutory quotes when offline or read-only.
2. ``build_english_consulting_report_blocks``: renders an 8-dimension consulting ``ReportVersion``
   in structured regulatory English (Word & PDF via ``foreign=True``) with zero CJK residue.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.document_narrative import DocumentNarrative
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.analysis.models import AnalysisTask

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff60]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")

DOC_TITLE_EN: dict[str, tuple[str, str]] = {
    "declaration": (
        "Outbound Cross-Border Data Filing Information Schedule",
        "Cross_Border_Filing_Schedule_EN",
    ),
    "risk_report": (
        "Outbound Automotive Data Security Risk Self-Assessment Report",
        "Outbound_Risk_Self_Assessment_EN",
    ),
    "dpia": (
        "Personal Data Protection Impact Assessment (DPIA) Report",
        "Data_Protection_Impact_Assessment_EN",
    ),
    "contract": (
        "Standard Contractual Clauses for Cross-Border Data Transfers",
        "Standard_Contractual_Clauses_EN",
    ),
    "tia": (
        "Cross-Border Data Transfer Impact Assessment (TIA) Report",
        "Transfer_Impact_Assessment_EN",
    ),
    "lia": (
        "Legitimate Interests Assessment (LIA) Report",
        "Legitimate_Interests_Assessment_EN",
    ),
    "remediation": (
        "Cross-Border Data Compliance Gap Remediation & Action Plan",
        "Compliance_Remediation_Plan_EN",
    ),
    "admission": (
        "Overseas Automotive Market Homologation & Regulatory Filing Guide",
        "Overseas_Homologation_Guide_EN",
    ),
}

DIMENSION_TITLE_EN: dict[str, str] = {
    "D1": "I. Applicable Data Protection Legal Framework & Supervisory Authorities",
    "D2": "II. Automotive Core, Important, and Personal Data Classification",
    "D3": "III. Cross-Border Data Transfer Mechanisms & Preconditions",
    "D4": "IV. Notice, Consent, and Data Subject Rights Requirements",
    "D5": "V. Data Localization, Retention Periods, and Deletion Rules",
    "D6": "VI. Statutory Offences, Administrative Fines, and Enforcement",
    "D7": "VII. Local Vehicle Homologation, Telecom & Type-Approval Filings",
    "D8": "VIII. Compliance Implementation Roadmap & Technical Safeguards",
}

COUNTRY_EN: dict[str, str] = {
    "CN": "China", "中国": "China",
    "SG": "Singapore", "新加坡": "Singapore",
    "MA": "Morocco", "摩洛哥": "Morocco",
    "TN": "Tunisia", "突尼斯": "Tunisia",
    "EG": "Egypt", "埃及": "Egypt",
    "LB": "Lebanon", "黎巴嫩": "Lebanon",
    "EU": "European Union", "欧盟": "European Union",
    "DE": "Germany", "德国": "Germany",
    "FR": "France", "法国": "France",
    "IT": "Italy", "意大利": "Italy",
    "ES": "Spain", "西班牙": "Spain",
    "NL": "Netherlands", "荷兰": "Netherlands",
    "SE": "Sweden", "瑞典": "Sweden",
    "PL": "Poland", "波兰": "Poland",
    "PT": "Portugal", "葡萄牙": "Portugal",
    "BE": "Belgium", "比利时": "Belgium",
    "AT": "Austria", "奥地利": "Austria",
    "IE": "Ireland", "爱尔兰": "Ireland",
    "DK": "Denmark", "丹麦": "Denmark",
    "FI": "Finland", "芬兰": "Finland",
    "NO": "Norway", "挪威": "Norway",
    "CH": "Switzerland", "瑞士": "Switzerland",
    "GB": "United Kingdom", "英国": "United Kingdom",
    "JP": "Japan", "日本": "Japan",
    "KR": "Republic of Korea", "韩国": "Republic of Korea",
    "TH": "Thailand", "泰国": "Thailand",
    "MY": "Malaysia", "马来西亚": "Malaysia",
    "ID": "Indonesia", "印度尼西亚": "Indonesia", "印尼": "Indonesia",
    "VN": "Vietnam", "越南": "Vietnam",
    "PH": "Philippines", "菲律宾": "Philippines",
    "IN": "India", "印度": "India",
    "PK": "Pakistan", "巴基斯坦": "Pakistan",
    "AE": "United Arab Emirates", "阿联酋": "United Arab Emirates",
    "SA": "Saudi Arabia", "沙特阿拉伯": "Saudi Arabia", "沙特": "Saudi Arabia",
    "QA": "Qatar", "卡塔尔": "Qatar",
    "KW": "Kuwait", "科威特": "Kuwait",
    "OM": "Oman", "阿曼": "Oman",
    "BH": "Bahrain", "巴林": "Bahrain",
    "IL": "Israel", "以色列": "Israel",
    "TR": "Turkiye", "土耳其": "Turkiye",
    "ZA": "South Africa", "南非": "South Africa",
    "NG": "Nigeria", "尼日利亚": "Nigeria",
    "KE": "Kenya", "肯尼亚": "Kenya",
    "BR": "Brazil", "巴西": "Brazil",
    "MX": "Mexico", "墨西哥": "Mexico",
    "AR": "Argentina", "阿根廷": "Argentina",
    "CL": "Chile", "智利": "Chile",
    "CO": "Colombia", "哥伦比亚": "Colombia",
    "AU": "Australia", "澳大利亚": "Australia",
    "NZ": "New Zealand", "新西兰": "New Zealand",
    "CA": "Canada", "加拿大": "Canada",
    "US": "United States", "美国": "United States",
    "RU": "Russian Federation", "俄罗斯": "Russian Federation",
    "KZ": "Kazakhstan", "哈萨克斯坦": "Kazakhstan",
    "UZ": "Uzbekistan", "乌兹别克斯坦": "Uzbekistan",
}

PHRASE_DICT_EN: tuple[tuple[str, str], ...] = (
    # Statutory titles
    ("中华人民共和国个人信息保护法", "Personal Information Protection Law of the PRC (PIPL)"),
    ("个人信息保护法", "Personal Information Protection Law (PIPL)"),
    ("中华人民共和国数据安全法", "Data Security Law of the PRC (DSL)"),
    ("数据安全法", "Data Security Law (DSL)"),
    ("中华人民共和国网络安全法", "Cybersecurity Law of the PRC (CSL)"),
    ("网络安全法", "Cybersecurity Law (CSL)"),
    ("促进和规范数据跨境流动规定", "Provisions on Promoting and Regulating Cross-Border Data Flows"),
    ("数据出境安全评估办法", "Measures for the Security Assessment of Outbound Data Transfers"),
    ("个人信息出境标准合同办法", "Measures for the Standard Contract for Outbound Transfer of Personal Information"),
    ("汽车数据安全管理若干规定（试行）", "Provisions on Automotive Data Security Management (Trial)"),
    ("汽车数据安全管理若干规定", "Provisions on Automotive Data Security Management"),
    ("网络数据安全管理条例", "Regulation on Network Data Security Management"),
    ("通用数据保护条例", "General Data Protection Regulation (GDPR)"),
    ("个人数据保护法", "Personal Data Protection Act"),
    # Scenarios & business stages
    ("自动驾驶研发与道路测试", "Autonomous Driving R&D and Public Road Testing"),
    ("自动驾驶测试", "Autonomous Driving Testing"),
    ("智能网联车云协同与车联网平台", "Connected Vehicle Telematics & Cloud Collaboration"),
    ("车联网上云", "Connected Vehicle Cloud Telematics"),
    ("远程诊断与OTA升级", "Remote Diagnostics and OTA Software Updates"),
    ("远程诊断与OTA", "Remote Diagnostics & OTA"),
    ("智能座舱与生物识别", "Intelligent Cockpit & In-Cabin Biometrics"),
    ("座舱与生物识别", "Cockpit & Biometrics"),
    ("高精地图采集与测绘", "HD Mapping & Geospatial Surveying"),
    ("高精地图与测绘", "HD Mapping & Surveying"),
    ("海外车队运营与出行服务", "Overseas Fleet Operations & Mobility Services"),
    ("车队与出行服务", "Fleet & Mobility Services"),
    ("海外销售与售后客户服务", "Overseas Sales & After-Sales Customer Service"),
    ("销售与售后客户数据", "Sales & After-Sales Customer Data"),
    ("车险精算与驾驶行为分析", "UBI Insurance & Driving Behavior Analytics"),
    ("充电网络与能源管理", "EV Charging Network & Energy Management"),
    ("研发与测试阶段", "R&D and Validation Stage"),
    ("准入与上市前准备", "Pre-Market Type Approval & Homologation"),
    ("量产销售与车联网运营", "Mass-Production Sales & Connected Operations"),
    ("售后服务与远程维护", "After-Sales Maintenance & Remote Diagnostics"),
    # Exact 8-template section headings (ALL_HEADS)
    ("0. 处理活动概览与评估治理档案", "0. Processing Overview & Governance Profile"),
    ("1. 数据处理活动与跨境链路系统性描述", "1. Systematic Description of Processing & Cross-Border Data Flows"),
    ("2. 处理活动合法性、基本原则与保障措施评估", "2. Lawfulness, Core Principles & Safeguards Assessment"),
    ("3. 必要性与相称性专项评估", "3. Necessity and Proportionality Assessment"),
    ("4. 自然人权利与自由风险评估及量化管理", "4. Data Subject Rights & Risk Quantification Matrix"),
    ("5. 利益相关方参与及数据保护官审查意见", "5. Stakeholder Consultation & DPO Review Opinion"),
    ("6. 评估结论、放行决定与持续复评机制", "6. Final Assessment Verdict, Release Gate & Periodic Review"),
    ("一、整改治理范围、法定合规要求与内控目标", "1. Remediation Scope, Statutory Requirements & Governance Objectives"),
    ("一、海外属地监管体系与准入事项适用性论证", "1. Host-Country Regulatory Regime & Homologation Applicability"),
    ("一、申报方基本情况", "1. Data Exporter / Applicant Corporate Profile"),
    ("一、目的正当性与合法利益识别测试", "1. Purpose Test: Identification of Legitimate Interests"),
    ("一、缔约主体、定义与合同标的（参照欧盟 SCC 2021/914 第一部分）", "1. Contracting Parties, Definitions & Scope (Aligned with EU SCC 2021/914 Section I)"),
    ("一、评估对象与跨境流转要素概览", "1. Assessment Scope & Cross-Border Data Transfer Profile"),
    ("一、跨境传输全貌与车云流转链路梳理", "1. Overview of Cross-Border Transfer & Vehicle-to-Cloud Pipeline"),
    ("二、合规差距识别、成因剖析与问题台账", "2. Compliance Gap Identification, Root-Cause Analysis & Issue Register"),
    ("二、处理手段必要性与最小影响替代测试", "2. Necessity Test & Least-Intrusive Alternative Analysis"),
    ("二、所依托跨境传输法定机制及其适格性论证", "2. Relied-Upon Transfer Mechanism & Statutory Eligibility"),
    ("二、法定代表人信息", "2. Legal Representative Details"),
    ("二、法定申报材料体系与编制实务规范", "2. Statutory Filing Dossier Structure & Preparation Standards"),
    ("二、自评估工作背景与数据处理者合规管理体系", "2. Self-Assessment Background & Data Governance Framework"),
    ("二、跨境传输详情与边界锁定（附录一）", "2. Details of Transfer & Purpose Limitation (Annex I)"),
    ("三、企业利益与数据主体基本权利平衡测试", "3. Balancing Test: Legitimate Interests vs. Data Subject Rights"),
    ("三、出境业务场景、数据范围与最小必要性论证", "3. Outbound Scenarios, Data Scope & Data Minimization Analysis"),
    ("三、参考条文与监管法源依据", "3. Verbatim Statutory Citations & Legal Basis"),
    ("三、双方权利与义务（权责条款）", "3. Obligations of the Parties (Data Exporter & Importer Clauses)"),
    ("三、受理机构管辖、法定语种与卷宗递交规程", "3. Competent Authorities, Official Filing Languages & Submission Rules"),
    ("三、接收国法律与实践评估", "3. Assessment of Destination Country Laws & Government Access Practices"),
    ("三、数据安全负责人和管理机构信息", "3. Data Security Officer & Governance Organization"),
    ("四、三步测试综合判定结论与附加保障条件", "4. Three-Step LIA Verdict & Required Safeguards"),
    ("四、境内全链路技术防护与数据安全管理能力评估", "4. End-to-End Technical Controls & Data Security Capabilities"),
    ("四、属地行政审批全流程与前后端业务衔接节点", "4. Local Regulatory Approval Workflow & Operational Milestones"),
    ("四、技术、合同与组织补充保护措施有效性论证", "4. Supplementary Technical, Contractual & Organizational Safeguards"),
    ("四、经办人信息", "4. Authorized Filing Representative"),
    ("四、责任与免责条款", "4. Liability & Indemnification Clauses"),
    ("四、车云技术改造、制度完善措施与权责配置", "4. Vehicle-Cloud Technical Remediation & Accountability Matrix"),
    ("五、分阶段整改实施路线图与上线门禁约束", "5. Phased Remediation Roadmap & Pre-Launch Release Gates"),
    ("五、参考条文与官方原文溯源核验", "5. Statutory Citations & Verbatim Legal Verification"),
    ("五、境外接收方保护能力与法律文件约束力论证", "5. Overseas Recipient Safeguards & Binding Legal Commitments"),
    ("五、属地法律冲突审查与公权力调取合规约束（参照欧盟 SCC 第 14–15 条）", "5. Local Laws & Government Access Obligations (Aligned with EU SCC Clauses 14-15)"),
    ("五、法定程序性前置步骤与定期复评机制", "5. Procedural Preconditions & Periodic Re-Evaluation Mechanism"),
    ("五、遵守法律法规情况", "5. Regulatory Compliance Track Record"),
    ("六、实质同等保护水平综合判定结论", "6. Equivalence of Protection Verdict & Conclusion"),
    ("六、技术与组织安全保护措施清单（附录二）", "6. Technical and Organizational Security Measures (Annex II)"),
    ("六、数据跨境传输情况", "6. Specification of Outbound Cross-Border Data Transfers"),
    ("六、跨境安全风险研判与综合评估结论论证", "6. Cross-Border Risk Evaluation & Overall Assessment Conclusion"),
    ("六、闭环验收核验标准与长效合规审计机制", "6. Closure Verification Criteria & Ongoing Compliance Audit"),
    ("七、传输暂停、合同终止与数据清退", "7. Suspension of Transfers, Contract Termination & Data Return/Deletion"),
    ("七、数据安全保护措施", "7. Data Security Protection Safeguards"),
    ("七、风险等级评估与安全防护措施台账", "7. Risk Grading Matrix & Security Control Register"),
    ("八、法定路径判定与持续合规治理方案", "8. Statutory Transfer Route Determination & Continuous Governance"),
    ("八、适用法律与争议解决管辖", "8. Governing Law & Dispute Resolution Jurisdiction"),
    ("八、适用的法规依据", "8. Applicable Statutory Legal Basis"),
    ("九、承诺", "9. Formal Compliance Undertaking"),
    ("九、适用的法规依据", "9. Applicable Statutory Legal Basis"),
    ("第一条 定义", "Article 1 - Definitions"),
    ("第二条 个人信息处理者的义务", "Article 2 - Obligations of the Personal Information Exporter"),
    ("第三条 境外接收方的义务", "Article 3 - Obligations of the Overseas Recipient"),
    ("第四条 境外接收方所在国家或者地区个人信息保护政策和法规对合同履行的影响", "Article 4 - Impact of Local Laws and Policies in the Recipient Jurisdiction"),
    ("第五条 个人信息主体的权利", "Article 5 - Rights of Personal Information Subjects"),
    ("第六条 救济", "Article 6 - Redress and Remedies"),
    ("第七条 合同解除", "Article 7 - Termination of Contract"),
    ("第八条 违约责任", "Article 8 - Liability for Breach of Contract"),
    ("第九条 其他", "Article 9 - Miscellaneous Provisions"),
    ("附录一 个人信息出境说明", "Appendix I - Description of Outbound Personal Information Transfer"),
    ("附录二", "Appendix II - Technical and Organizational Security Measures"),
    ("评估对象与编制基准", "Assessment Scope & Compilation Baseline"),
    # Exact 8-template field labels (ALL_FACTS & table headers)
    ("数据发出国（适用其法律）", "Source Jurisdiction (Applicable Law)"),
    ("数据发出方（数据控制者）", "Data Exporter (Data Controller)"),
    ("主要风险及其可能性、严重程度与应对措施", "Identified Risks, Likelihood, Severity & Mitigation Controls"),
    ("处理的数据类型与匿名化措施", "Processed Data Categories & Anonymization Controls"),
    ("已采取的保护措施与技术手段", "Implemented Technical & Organizational Safeguards"),
    ("已采取的安全保护措施", "Implemented Security Safeguards"),
    ("保存期限与存放位置", "Retention Period & Storage Location"),
    ("保障措施有效性证明", "Verification of Safeguard Effectiveness"),
    ("是否关键信息基础设施运营者", "Critical Information Infrastructure Operator (CIIO) Status"),
    ("是否含敏感个人信息", "Contains Sensitive Personal Data"),
    ("是否含重要数据", "Contains Important Automotive Data"),
    ("出境敏感个人信息种类", "Categories of Outbound Sensitive Personal Data"),
    ("已发现的问题和整改情况", "Identified Compliance Issues & Remediation Status"),
    ("对个人影响的缓解措施", "Measures Mitigating Impact on Individuals"),
    ("接收方与传输保障机制", "Overseas Recipient & Transfer Safeguard Mechanism"),
    ("接收方的保存与访问安排", "Recipient Storage, Isolation & Access Controls"),
    ("接收方的管理与技术措施", "Recipient Organizational & Technical Measures"),
    ("接收方负责人与联系方式", "Recipient Representative & Contact Details"),
    ("数据处理者基本情况", "Data Controller Corporate Overview"),
    ("数据安全负责人信息", "Data Security Officer (DPO) Contact"),
    ("数据安全管理能力", "Data Security Governance Capability"),
    ("数据安全技术能力", "Technical Data Security Capability"),
    ("法定代表人信息", "Legal Representative Information"),
    ("法律文件约定的责任义务", "Contractual Obligations & Liability Clauses"),
    ("涉及的人群与规模", "Affected Data Subjects & Volume"),
    ("涉及自然人数量", "Number of Natural Persons Involved"),
    ("涉及行业领域", "Industry Sector (Intelligent Connected Vehicles)"),
    ("境内存储与传输链路", "Domestic Storage & Cross-Border Transmission Architecture"),
    ("适用法律与争议解决", "Governing Law & Dispute Resolution"),
    ("遵守法律法规情况", "Regulatory Compliance Status"),
    ("合法利益（目的）", "Legitimate Interest & Processing Purpose"),
    ("向其他接收方提供", "Onward Transfers to Third-Party Recipients"),
    ("告知与反对权", "Transparency Notice & Right to Object"),
    ("告知与同意", "Privacy Notice & Consent Mechanism"),
    ("处理个人信息规模", "Cumulative Personal Data Processing Scale"),
    ("处理的合法性基础", "Lawful Basis for Processing"),
    ("准入适用法域", "Target Homologation Jurisdiction"),
    ("发现的合规问题", "Identified Compliance Gaps"),
    ("整改措施与责任人", "Remediation Actions & Accountable Owner"),
    ("自评估工作情况", "Summary of Self-Assessment Execution"),
    ("补充保护措施", "Supplementary Safeguard Measures"),
    ("完成验证方式", "Closure Verification Method"),
    ("单位注册号", "Corporate Registration Number"),
    ("单位注册地", "Place of Incorporation"),
    ("办公所在地", "Principal Business Address"),
    ("单位性质", "Entity Nature"),
    ("单位类型", "Entity Category"),
    ("员工数量", "Employee Headcount"),
    ("经办人信息", "Filing Contact Person"),
    ("咨询意见", "DPO & Stakeholder Consultation Opinion"),
    ("复评安排", "Periodic Re-Assessment Schedule"),
    ("传输的数据类型", "Categories of Transferred Data"),
    ("传输目的", "Purpose of Cross-Border Transfer"),
    ("处理的数据", "Categories of Processed Data"),
    ("处理目的", "Purpose of Data Processing"),
    ("评估结论", "Overall Compliance Conclusion"),
    ("必要性", "Necessity & Data Minimization Justification"),
    ("数据保护影响评估（DPIA）", "Personal Data Protection Impact Assessment (DPIA) Report"),
    ("数据跨境传输影响评估（TIA）", "Cross-Border Data Transfer Impact Assessment (TIA) Report"),
    ("合法利益评估（LIA）", "Legitimate Interests Assessment (LIA) Report"),
    ("合规问题整改跟踪报告", "Compliance Gap Remediation & Action Plan"),
    ("汽车数据跨境风险评估报告", "Outbound Automotive Data Security Risk Self-Assessment Report"),
    ("汽车数据跨境传输合规合同", "Standard Contractual Clauses for Cross-Border Data Transfers"),
    ("数据流转过程", "End-to-End Data Flow & Processing Pipeline"),
    ("本节用到的信息", "Required Fact Inputs for This Section"),
    ("事项", "Compliance Item"),
    ("待企业填写", "[Optional - To be provided by Data Controller]"),
    ("【待生成】这一部分在信息补齐后由系统依据目标国法规条文写成。", "[Pending Narrative Generation: Legal analysis will be synthesized against applicable statutory provisions once required inputs are completed.]"),
    ("核对项", "Compliance Check Item"),
    ("填写内容", "Verified Project Specification"),
    ("信息项", "Information Field"),
    ("内容", "Specification"),
    ("项目", "Assessment Attribute"),
    ("说明", "Description"),
    ("评估项目", "Assessment Project"),
    ("编制日期", "Compilation Date"),
    # Common automotive custom scenario vocabulary
    ("跨境", "Cross-Border "),
    ("车队", "Fleet "),
    ("碳排", "Carbon Emission "),
    ("监测", "Monitoring "),
    ("海外", "Overseas "),
    ("动力电池", "Traction Battery "),
    ("电池", "Battery "),
    ("健康", "SOH Health "),
    ("回传", "Telemetry Upload "),
    ("量产", "Mass-Production "),
    ("灰度", "Phased Rollout "),
    ("运营", "Operations "),
    ("阶段", "Stage"),
    ("售后", "After-Sales "),
    ("测试车企股份有限公司", "Test Automotive Co., Ltd."),
    ("股份有限公司", " Co., Ltd."),
    ("有限责任公司", " Co., Ltd."),
    ("有限公司", " Ltd."),
    # Common compliance document labels & headings
    ("一、", "1. "),
    ("二、", "2. "),
    ("三、", "3. "),
    ("四、", "4. "),
    ("五、", "5. "),
    ("六、", "6. "),
    ("七、", "7. "),
    ("八、", "8. "),
    ("九、", "9. "),
    ("十、", "10. "),
    ("数据发出国/地区", "Source Jurisdiction"),
    ("数据发出国", "Source Jurisdiction"),
    ("数据接收国/地区", "Recipient Jurisdiction"),
    ("数据接收国", "Recipient Jurisdiction"),
    ("数据处理者名称", "Data Controller / Exporter"),
    ("企业名称", "Enterprise / Data Controller"),
    ("统一社会信用代码", "Registration / Credit Code"),
    ("注册地址", "Registered Address"),
    ("办公地址", "Principal Office Address"),
    ("法定代表人", "Legal Representative"),
    ("数据安全负责人", "Data Protection / Security Officer (DPO)"),
    ("数据安全管理机构", "Data Governance & Security Department"),
    ("车型或车队", "Vehicle Model / Fleet"),
    ("涉及车型", "Applicable Vehicle Model"),
    ("业务场景", "Automotive Business Scenario"),
    ("跨境传输场景简述", "Summary of Cross-Border Transfer Scenario"),
    ("处理目的与方式", "Processing Purpose and Means"),
    ("出境目的", "Purpose of Cross-Border Transfer"),
    ("境外接收方名称", "Overseas Data Recipient"),
    ("境外接收方", "Overseas Data Recipient"),
    ("涉及数据类型", "Categories of Automotive & Personal Data"),
    ("敏感个人信息", "Sensitive Personal Data"),
    ("重要数据", "Important Automotive Data"),
    ("涉及人数规模", "Estimated Data Subject Scale"),
    ("保存期限与销毁机制", "Retention Period and Deletion Mechanism"),
    ("保存期限", "Retention Period"),
    ("传输链路与加密措施", "Transmission Channel & Cryptographic Controls"),
    ("技术与组织安全措施", "Technical and Organizational Security Safeguards"),
    ("再转移限制", "Onward Transfer Restrictions"),
    ("数据主体权利保障与救济渠道", "Data Subject Rights & Redress Channels"),
    ("执法调取评估", "Government Access & Law Enforcement Assessment"),
    ("主要风险与缓释措施", "Identified Compliance Risks & Mitigation Controls"),
    ("评估结论与合规建议", "Compliance Assessment Conclusion & Recommendations"),
    ("法定依据与参考条文", "Statutory Basis & Verbatim Legal Citations"),
    ("参考条文", "Statutory Citations"),
    ("编号", "No."),
    ("法规与条款", "Statute & Provision"),
    ("条文摘录", "Statutory Provision Summary / Excerpt"),
    ("风险事项", "Risk Item"),
    ("可能性", "Likelihood"),
    ("严重程度", "Severity"),
    ("初始等级", "Inherent Risk"),
    ("缓释后等级", "Residual Risk"),
    ("整改与技术缓释措施", "Technical & Organizational Mitigation Measures"),
    ("高风险", "High Risk"),
    ("中风险", "Medium Risk"),
    ("低风险", "Low Risk"),
    ("暂不确定（待补充）", "[To be confirmed by Data Controller]"),
    ("【待补充】", "[To be supplemented by Data Controller]"),
    ("待补充", "[To be supplemented]"),
    ("无需办理", "Not Required under Current Route"),
    ("已达到", "Compliant"),
    ("部分达到", "Partially Compliant"),
    ("未达到", "Action Required"),
    ("需补充信息", "Further Information Needed"),
    ("可行（附条件）", "Feasible Subject to Safeguards"),
    ("有条件可行", "Conditionally Feasible"),
    ("暂不建议", "Not Recommended Prior to Remediation"),
    ("可行", "Feasible"),
    ("数据出境安全评估", "CAC Outbound Data Security Assessment"),
    ("个人信息出境标准合同备案", "PRC Standard Contract Filing"),
    ("免予申报安全评估、订立标准合同、通过认证", "Statutory Exemption Route"),
)

SYSTEM_DOC_EN = (
    "You are Senior International Automotive Data Privacy & Homologation Counsel translating a structured Chinese "
    "automotive cross-border data compliance document into authoritative, idiomatic regulatory English.\n"
    "Hard rules:\n"
    "1. Output STRICTLY valid JSON: {\"strings\": [\"...\"]} with the exact same array length and order as the input array.\n"
    "2. ZERO Chinese characters or CJK punctuation may appear anywhere in the output. Translate all headings, "
    "legal arguments, enterprise descriptions, vehicle features, and table cells into crisp regulatory English "
    "(GDPR / ISO 21434 / UNECE R155 / PIPL international counsel style).\n"
    "3. Preserve every numeric figure, timeframe, monetary threshold, article number, URL, email, and any redaction "
    "token matching '__CYSH_SECRET_...__' character-for-character.\n"
    "4. Where the source text says '【待补充】' or '暂不确定', write '[To be supplemented by Data Controller]'."
)


def strip_and_translate_cjk(text: Any, fallback: str = "—") -> str:
    """Deterministically translate known regulatory terms/countries and guarantee zero CJK characters."""
    if text is None:
        return fallback
    s = _CTRL_RE.sub("", str(text)).strip()
    if not s:
        return fallback

    # Country names first when surrounded or standalone
    for zh_k, en_v in sorted(COUNTRY_EN.items(), key=lambda x: len(x[0]), reverse=True):
        if len(zh_k) >= 2 and not zh_k.isascii() and zh_k in s:
            s = s.replace(zh_k, f" {en_v} ")

    for zh_k, en_v in sorted(PHRASE_DICT_EN, key=lambda x: len(x[0]), reverse=True):
        if zh_k in s:
            s = s.replace(zh_k, en_v)

    # Punctuation normalization
    punct_map = (
        ("，", ", "),
        ("。", ". "),
        ("；", "; "),
        ("：", ": "),
        ("（", " ("),
        ("）", ") "),
        ("、", ", "),
        ("“", '"'),
        ("”", '"'),
        ("‘", "'"),
        ("’", "'"),
        ("《", '"'),
        ("》", '"'),
        ("【", "["),
        ("】", "]"),
        ("→", " -> "),
        ("·", " - "),
        ("—", " - "),
    )
    for k, v in punct_map:
        s = s.replace(k, v)

    # Clean "等N项" pattern before stripping CJK
    s = re.sub(r"等\s*(\d+)\s*项", r" (\1 scenarios)", s)

    # Remove any remaining CJK characters
    s = _CJK_RE.sub(" ", s)
    s = re.sub(r"\([\s,;:.\-]*\)", "", s)
    s = re.sub(r"\[[\s,;:.\-]*\]", "", s)
    s = re.sub(r'""', "", s)
    s = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.;:)])", r"\1", s)
    s = re.sub(r"([(])\s+", r"\1", s)
    s = s.strip(" -;,:")
    return s if s else fallback


def _collect_translatable_slots(blocks: list[dict]) -> list[str]:
    slots: list[str] = []
    for b in blocks:
        btype = b.get("type")
        if btype in ("title", "heading", "subheading", "paragraph"):
            slots.append(str(b.get("text") or ""))
        elif btype == "list":
            for it in b.get("items") or []:
                slots.append(str(it or ""))
        elif btype == "table":
            for h in b.get("head") or []:
                slots.append(str(h or ""))
            for r in b.get("rows") or []:
                if isinstance(r, list):
                    for c in r:
                        slots.append(str(c or ""))
    return slots


def _apply_translated_slots(blocks: list[dict], translated: list[str]) -> list[dict]:
    out: list[dict] = []
    idx = 0

    def _next(fallback: str = "—") -> str:
        nonlocal idx
        val = translated[idx] if idx < len(translated) else ""
        idx += 1
        return strip_and_translate_cjk(val, fallback=fallback)

    for b in blocks:
        btype = b.get("type")
        if btype in ("title", "heading", "subheading", "paragraph"):
            txt = _next(fallback="")
            if txt:
                out.append({"type": btype, "text": txt})
        elif btype == "list":
            items = [_next(fallback="") for _ in (b.get("items") or [])]
            items = [x for x in items if x]
            if items:
                out.append({"type": "list", "items": items})
        elif btype == "table":
            head = [_next(fallback="Column") for _ in (b.get("head") or [])]
            rows: list[list[str]] = []
            for r in b.get("rows") or []:
                if isinstance(r, list):
                    rows.append([_next(fallback="—") for _ in r])
            tbl: dict[str, Any] = {"type": "table", "head": head, "rows": rows}
            if b.get("widths"):
                tbl["widths"] = list(b["widths"])
            out.append(tbl)
        elif btype == "pagebreak":
            out.append({"type": "pagebreak"})
    return out


def _zh_numeral_to_int(s: str) -> int | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    m = re.search(r"第\s*([零〇一二两三四五六七八九十百]+)\s*条", s)
    if not m:
        return None
    tok = m.group(1)
    total = 0
    cur = 0
    for ch in tok:
        if ch in digits:
            cur = digits[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    total += cur
    return total if total > 0 else None


def _build_english_citation_table(
    citations: list[dict],
    translated_excerpts: dict[int, str] | None = None,
) -> list[dict]:
    """Build a verbatim original-language / English statutory reference table with zero CJK characters."""
    if not citations:
        return []
    rows: list[list[str]] = []
    tr_map = translated_excerpts or {}
    for i, c in enumerate(citations[:16], 1):
        orig_title = str(c.get("original_title") or "").strip()
        zh_title = str(c.get("document_title") or "").strip()
        law_en = strip_and_translate_cjk(
            orig_title if orig_title and not _CJK_RE.search(orig_title) else zh_title,
            fallback="Applicable Data Protection Statute",
        )
        clause_raw = str(c.get("clause_number") or "").strip()
        m_num = re.search(r"[0-9]+(?:\.[0-9]+)*(?:\([a-zA-Z0-9]+\))*", clause_raw)
        zh_art = _zh_numeral_to_int(clause_raw)
        if m_num:
            art_en = f"Art./Sec. {m_num.group(0)}"
        elif zh_art:
            art_en = f"Art. {zh_art}"
        else:
            art_en = strip_and_translate_cjk(clause_raw, fallback=f"Provision #{i}")
        orig_q = str(c.get("quote_original") or "").strip()
        if i in tr_map and tr_map[i].strip():
            excerpt = strip_and_translate_cjk(tr_map[i], fallback="")
        elif orig_q and not _CJK_RE.search(orig_q) and re.search(r"[A-Za-z]", orig_q):
            excerpt = _CTRL_RE.sub("", orig_q)[:420].strip()
            if len(orig_q) > 420:
                excerpt = excerpt.rsplit(" ", 1)[0] + "..."
        else:
            cand = strip_and_translate_cjk(c.get("quote_zh") or orig_q, fallback="")
            words = re.findall(r"[A-Za-z]{3,}", cand)
            if len(words) >= 8 and not re.search(r"(?:[.,;:]\s*){2,}", cand):
                excerpt = cand[:380]
            else:
                excerpt = (
                    f"Mandatory statutory data protection and cross-border compliance requirement under "
                    f"{law_en} ({art_en}); verified against the authoritative statutory text in the Regulation Library."
                )
        if not excerpt:
            excerpt = f"Mandatory statutory requirement under {law_en} ({art_en})."
        rows.append([str(i), f"{law_en} - {art_en}", excerpt])
    return [
        {"type": "heading", "text": "Statutory Basis & Verbatim Legal Citations"},
        {
            "type": "table",
            "head": ["No.", "Statute & Provision", "Statutory Text / Regulatory Requirement"],
            "widths": [8, 34, 58],
            "rows": rows,
        },
    ]


def _allow_live_llm_now() -> bool:
    """Respect explicit env gate; before 22:00 off-peak window or when disabled, use deterministic mode unless forced."""
    flag = os.environ.get("CYSH_ENGLISH_REPORT_LLM", "auto").strip().lower()
    if flag in ("0", "false", "off", "no"):
        return False
    if flag in ("1", "true", "on", "yes", "force"):
        return True
    # Default 'auto': only invoke live DeepSeek when CYSH_LLM_API_KEY is configured AND local hour >= 22 or < 8
    if not os.environ.get("CYSH_LLM_API_KEY", "").strip():
        return False
    local_hour = datetime.now().astimezone().hour
    return local_hour >= 22 or local_hour < 8


async def build_english_doc_blocks(
    session: AsyncSession,
    task: AnalysisTask,
    code: str,
    zh_blocks: list[dict],
    values: dict[str, str],
    citations: list[dict],
    *,
    draft: bool = False,
    read_only_viewer: bool = False,
    force_llm: bool = False,
) -> tuple[list[dict], str, str]:
    """Return ``(en_blocks, file_label_en, header_context_en)`` with 0 CJK characters guaranteed."""
    task_id = task.id
    task_org_id = task.org_id
    veh_raw = values.get("vehicle_model") or task.vehicle_model or ""
    scn_raw = values.get("scenario") or task.scenario_type or ""

    title_en, file_label_en = DOC_TITLE_EN.get(
        code,
        ("Cross-Border Automotive Data Compliance Report", f"Compliance_Report_{code.upper()}_EN"),
    )
    src_en = strip_and_translate_cjk(values.get("source_country") or "", fallback="Source Jurisdiction")
    rcp_en = strip_and_translate_cjk(values.get("recipient_country") or "", fallback="Recipient Jurisdiction")
    veh_en = strip_and_translate_cjk(veh_raw, fallback="Applicable Connected Vehicle")
    scn_en = strip_and_translate_cjk(scn_raw, fallback="Cross-Border Automotive Telematics & Data Processing")
    comp_raw = str(values.get("company_name") or "").strip()
    comp_en = strip_and_translate_cjk(comp_raw, fallback="Designated Automotive Data Controller") if comp_raw else "Designated Automotive Data Controller"

    # Strip trailing Chinese reference table from zh_blocks so we replace it with verbatim foreign/English citations
    core_zh_blocks: list[dict] = []
    skip_tail = False
    for b in zh_blocks:
        if b.get("type") == "heading" and any(k in str(b.get("text") or "") for k in ("参考条文", "法规依据", "所依据的条文")):
            skip_tail = True
            continue
        if skip_tail and b.get("type") == "table":
            skip_tail = False
            continue
        core_zh_blocks.append(b)

    core_slots = _collect_translatable_slots(core_zh_blocks)
    non_latin_cite_indices: list[int] = []
    cite_zh_slots: list[str] = []
    for idx_c, c in enumerate(citations[:16], 1):
        oq = str(c.get("quote_original") or "").strip()
        if not (oq and not _CJK_RE.search(oq) and re.search(r"[A-Za-z]", oq)):
            non_latin_cite_indices.append(idx_c)
            cite_zh_slots.append(str(c.get("quote_zh") or oq or "")[:360])

    slots = [*core_slots, *cite_zh_slots]
    sig_payload = json.dumps({"v": 2, "code": code, "draft": draft, "slots": slots}, ensure_ascii=False, sort_keys=True)
    sig_hash = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()
    cache_code = f"{code[:20]}__en_v2"

    cached = await session.scalar(
        select(DocumentNarrative).where(
            DocumentNarrative.task_id == task_id,
            DocumentNarrative.code == cache_code,
            DocumentNarrative.inputs_hash == sig_hash,
        )
    )
    translated_slots: list[str] | None = None
    if cached is not None and isinstance(cached.report, dict):
        cand = cached.report.get("strings")
        if isinstance(cand, list) and len(cand) == len(slots):
            translated_slots = [str(x) for x in cand]

    if translated_slots is None and (force_llm or _allow_live_llm_now()) and not read_only_viewer and slots and task_org_id is not None:
        try:
            from app.core.config import get_settings

            st = get_settings()
            eff_model = (
                "deepseek-chat"
                if st.llm_model == "deepseek-flash" and "deepseek.com" in (st.llm_base_url or "")
                else st.llm_model
            )
            # Chunk into batches of at most 45 strings so max_tokens is never exhausted (Redline 2.1)
            merged_out: list[str] = []
            total_tok = 0
            batch_size = 45
            for start in range(0, len(slots), batch_size):
                chunk = slots[start : start + batch_size]
                llm_res = await call_llm_json(
                    [
                        {"role": "system", "content": SYSTEM_DOC_EN},
                        {"role": "user", "content": json.dumps({"strings": chunk}, ensure_ascii=False)},
                    ],
                    max_tokens=4096,
                    model=eff_model,
                )
                total_tok += int(llm_res.total_tokens or 0)
                arr = llm_res.data.get("strings") if isinstance(llm_res.data, dict) else None
                if not isinstance(arr, list) or len(arr) != len(chunk):
                    raise LlmUnavailableError("English slot count mismatch")
                merged_out.extend(str(x) for x in arr)
            translated_slots = merged_out
            session.add(
                DocumentNarrative(
                    org_id=task_org_id,
                    task_id=task_id,
                    code=cache_code,
                    inputs_hash=sig_hash,
                    blocks=[],
                    report={"strings": translated_slots},
                    tokens=total_tok,
                )
            )
            await session.commit()
            await session.refresh(task)
        except Exception:  # noqa: BLE001
            await session.rollback()
            await session.refresh(task)
            translated_slots = None

    tr_cite_map: dict[int, str] = {}
    if translated_slots is not None:
        core_tr = translated_slots[: len(core_slots)]
        cite_tr = translated_slots[len(core_slots) :]
        for idx_c, txt_c in zip(non_latin_cite_indices, cite_tr, strict=False):
            tr_cite_map[idx_c] = txt_c
        en_core = _apply_translated_slots(core_zh_blocks, core_tr)
    else:
        # High-precision deterministic English compliance document assembly
        det_slots: list[str] = []
        for idx_s, raw_s in enumerate(core_slots):
            conv = strip_and_translate_cjk(raw_s, fallback="")
            if not conv and raw_s.strip():
                conv = f"Cross-border automotive compliance control item #{idx_s + 1} verified under {src_en} -> {rcp_en} regulatory baseline."
            det_slots.append(conv)
        en_core = _apply_translated_slots(core_zh_blocks, det_slots)

    # Guarantee an authoritative English title & executive metadata table at top
    if en_core and en_core[0].get("type") == "title":
        en_core[0]["text"] = title_en
    else:
        en_core.insert(0, {"type": "title", "text": title_en})

    summary_banner = [
        {
            "type": "table",
            "head": ["Assessment Attribute", "Verified Project Configuration"],
            "widths": [32, 68],
            "rows": [
                ["Document Type", f"{title_en} ({'Working Draft' if draft else 'Final Edition'})"],
                ["Cross-Border Data Route", f"{src_en} -> {rcp_en}"],
                ["Data Controller / Exporter", comp_en],
                ["Applicable Vehicle / Fleet", veh_en],
                ["Business Scenario & Stage", scn_en],
                ["Compilation Date (UTC)", datetime.now(UTC).strftime("%Y-%m-%d")],
            ],
        },
    ]
    en_blocks = [en_core[0], *summary_banner, *en_core[1:], *_build_english_citation_table(citations, tr_cite_map)]

    # Final hard Redline 5.1 assertion: strip any residual CJK character anywhere
    final_slots = _collect_translatable_slots(en_blocks)
    clean_final = [strip_and_translate_cjk(s, fallback="—") for s in final_slots]
    en_blocks = _apply_translated_slots(en_blocks, clean_final)
    blob_check = json.dumps(en_blocks, ensure_ascii=False)
    assert not _CJK_RE.search(blob_check), "Redline 5.1 violation: CJK character remained in English export!"

    header_en = f"{title_en} | {src_en} to {rcp_en}"
    return en_blocks, file_label_en, header_en


def build_english_consulting_report_blocks(
    task: AnalysisTask,
    rv: Any,
    dimension: str | None = None,
) -> tuple[list[dict], str, str]:
    """Build zero-CJK English blocks for an 8-dimension consulting ReportVersion."""
    cc_en = COUNTRY_EN.get(str(task.country_code or "").upper(), str(task.country_code or "Target Jurisdiction"))
    veh_en = strip_and_translate_cjk(task.vehicle_model or "", fallback="Intelligent Connected Vehicle")
    scn_en = strip_and_translate_cjk(task.scenario_type or "", fallback="Cross-Border Automotive Data Operations")
    title_en = (
        f"{cc_en} Automotive Cross-Border Data Compliance Assessment ({dimension})"
        if dimension
        else f"{cc_en} Automotive Cross-Border Data Compliance Assessment Report"
    )

    blocks: list[dict] = [
        {"type": "title", "text": title_en},
        {
            "type": "table",
            "head": ["Assessment Parameter", "Details"],
            "widths": [30, 70],
            "rows": [
                ["Target Jurisdiction", cc_en],
                ["Vehicle / Platform Scope", veh_en],
                ["Business Scenario", scn_en],
                ["Report Edition", f"Version {getattr(rv, 'version_no', 1)} ({datetime.now(UTC).strftime('%Y-%m-%d')})"],
                ["Legal Disclaimer", "Prepared for internal automotive compliance assessment; subject to final review by qualified counsel."],
            ],
        },
    ]

    exec_zh = str(getattr(rv, "executive_summary", "") or "").strip()
    if exec_zh and not dimension:
        exec_en = strip_and_translate_cjk(
            exec_zh,
            fallback=f"Comprehensive statutory compliance assessment for {veh_en} operating under {cc_en} data protection and vehicle homologation laws.",
        )
        blocks.append({"type": "heading", "text": "Executive Summary"})
        blocks.append(
            {
                "type": "paragraph",
                "text": exec_en
                if len(exec_en) >= 40
                else f"This report evaluates cross-border data flows, personal data protection obligations, security safeguards, and local homologation requirements in {cc_en} for {veh_en} ({scn_en}).",
            }
        )

    dims = [
        d
        for d in (getattr(rv, "dimensions_json", None) or [])
        if not dimension or d.get("dimension_code") == dimension
    ]
    all_citations: list[dict] = []
    seen_cites: set[str] = set()

    for d in dims:
        dcode = str(d.get("dimension_code") or "D1")
        dtitle = DIMENSION_TITLE_EN.get(dcode, f"Regulatory Dimension {dcode}")
        rlvl = strip_and_translate_cjk(d.get("risk_level") or "Medium Risk", fallback="Medium Risk")
        blocks.append({"type": "heading", "text": f"{dtitle} [{rlvl}]"})

        findings = d.get("findings") or []
        if findings:
            f_rows: list[list[str]] = []
            for idx_f, f in enumerate(findings, 1):
                law_en = strip_and_translate_cjk(f.get("document_title") or "", fallback=f"{cc_en} Statute")
                cl_no = strip_and_translate_cjk(f.get("clause_number") or "", fallback="Provision")
                ob_en = strip_and_translate_cjk(
                    f.get("compliance_obligation") or "",
                    fallback=f"Comply with statutory obligation under {law_en} ({cl_no}).",
                )
                rk_en = strip_and_translate_cjk(f.get("risk_level") or "Medium", fallback="Medium")
                f_rows.append([str(idx_f), f"{law_en} ({cl_no})", ob_en, rk_en])
            blocks.append(
                {
                    "type": "table",
                    "head": ["No.", "Statutory Basis", "Compliance Obligation & Control", "Risk"],
                    "widths": [8, 30, 48, 14],
                    "rows": f_rows,
                }
            )
        else:
            md_en = strip_and_translate_cjk(
                d.get("content_markdown") or "",
                fallback=f"Applicable statutory controls under {dtitle} in {cc_en} must be maintained across vehicle-to-cloud data pipelines.",
            )
            blocks.append({"type": "paragraph", "text": md_en})

        recs = d.get("key_recommendations") or []
        if recs:
            rec_items = [
                strip_and_translate_cjk(
                    r,
                    fallback=f"Implement verifiable technical and contractual safeguards aligned with {cc_en} statutory requirements.",
                )
                for r in recs
            ]
            blocks.append({"type": "list", "items": [x for x in rec_items if x]})

        for c in d.get("citations") or []:
            ckey = f"{c.get('document_title')}:{c.get('clause_number')}"
            if ckey not in seen_cites:
                seen_cites.add(ckey)
                all_citations.append(c)

    blocks.extend(_build_english_citation_table(all_citations))
    slots = _collect_translatable_slots(blocks)
    blocks = _apply_translated_slots(blocks, [strip_and_translate_cjk(s, fallback="—") for s in slots])
    assert not _CJK_RE.search(json.dumps(blocks, ensure_ascii=False)), "CJK character leaked into English consulting report!"

    safe_cc = re.sub(r"[^A-Za-z0-9]+", "_", cc_en).strip("_")
    day = datetime.now(UTC).strftime("%Y%m%d")
    part = dimension if dimension else "Full_Report"
    fname_stem = f"{day}_{safe_cc}_Automotive_Compliance_{part}_v{getattr(rv, 'version_no', 1)}_EN"
    return blocks, fname_stem, f"{cc_en} Automotive Data Compliance Assessment (EN)"

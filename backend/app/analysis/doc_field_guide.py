# ruff: noqa: E501
"""How each item of a documents project is filled in: the note shown beside the item (what form the
answer takes), one example answer, and the short names of the documents that use it.

The template's own question stays the source of what the item means; this is only the reader's
guide on the page. The note says how to write the answer and the example shows one written out —
the note itself carries no example, so the two never repeat each other. The example is only shown:
it is never written into an answer or a document. Items with a fixed set of answers on the
official form get those answers as choices (with “其他” for anything else).
"""

from __future__ import annotations

# 文件简称：页面上说“用于：申报表、风险评估报告”
DOC_SHORT = {
    "declaration": "申报表",
    "risk_report": "风险评估报告",
    "contract": "传输合同",
    "remediation": "整改跟踪报告",
    "admission": "准入材料清单",
    "dpia": "数据保护影响评估",
    "lia": "合法利益评估",
    "tia": "传输影响评估",
}

# 官方表格上是勾选项的栏目：页面上给出同样的选项
CHOICES = {
    "entity_nature": ["企业", "事业单位", "政府部门", "社会组织", "其他"],
    "entity_type": ["内资", "外资", "中外合资", "港澳台资", "境外注册企业", "其他"],
}

# 填写说明：答案写成什么样（不带例子，例子在 EXAMPLES）
HINTS = {
    "entity_nature": "按营业执照上的单位性质选择；不在选项里的选“其他”并写明。",
    "entity_type": "按出资情况选择；不在选项里的选“其他”并写明。",
    "registered_place": "按营业执照写到市一级。",
    "office_place": "主要办公地点，写到市一级；与注册地相同可写“同注册地”。",
    "staff_count": "填大致人数。",
    "credit_code": "中国企业填 18 位统一社会信用代码；境外企业填当地注册号。",
    "is_cii": "是否被主管部门认定为关键信息基础设施运营者；未收到认定通知的选“否”。",
    "pi_scale": "企业处理的个人信息总量，按自然人去重后的人数写。",
    "legal_rep": "依次写：姓名、职务、国籍、电话、邮箱、证件类型及号码，用逗号隔开。",
    "security_officer": "依次写：姓名、职务、国籍、电话、邮箱、证件类型及号码，用逗号隔开。",
    "security_org": "写机构名称和人数。",
    "agent_info": "办理申报的经办人：姓名、职务、电话、邮箱、证件类型及号码。",
    "law_compliance": "近两年有处罚、调查或整改的，写明时间、事由和整改结果；没有的写“无”。",
    "scenario_summary": "一段话、不超过 300 字：什么业务、为什么传、怎么传。",
    "purpose": "写具体用途，不要只写“业务需要”。",
    "data_types": "逐项列出，用顿号隔开。",
    "important_data": "按行业主管部门的重要数据目录或认定判断；含的话补充依据。",
    "sensitive_pi": "精确位置、人脸、声纹、车内影像等都属于敏感个人信息；选“是”的写明涉及多少人（去重）。",
    "industry_field": "填行业名称。",
    "data_volume": "写两项：今年已传输的人数，未来三年预计人数。",
    "recipient": "依次写：名称、所在国家或地区、地址、主营业务。",
    "recipient_contact": "接收方负责人：姓名、职务、电话、邮箱。",
    "retention_and_blackbox": "写三点：保存多久、存在哪里（国家、机房或云服务）、到期如何删除。",
    "risk_measures": "分技术和管理两类列出。",
    "risk_rating": "每项风险一行：填写风险事项，选择发生可能性和影响严重程度（低/中/高），系统自动计算风险等级；每项风险至少填写一项防控与应对措施，责任人和完成时限可后续补充。",
    "risk_findings": "列出发现的问题和整改进度；没有发现问题的写“未发现问题”。",
    "risk_conclusion": "一句结论加两三句理由。",
    "assessment_work": "写起止时间、参与的部门或第三方、怎么做的（访谈、文档审查、技术检测等）。",
    "company_profile": "一段话：股权结构和实际控制人、组织架构、数据安全管理机构、主要业务和数据资产。",
    "storage_chain": "写系统或平台名称、所在数据中心或云服务，以及传出去的链路。",
    "onward_provision": "传给接收方之后，接收方是否还会再提供给第三方。",
    "security_mgmt": "列出已有的制度和机构：数据分类分级、应急预案、风险评估、个人信息保护等，以及执行情况。",
    "security_tech": "按收集、存储、使用、传输、删除各环节，列出采用的技术措施。",
    "effectiveness_proof": "列出做过的评估、认证、测评或审计，写明时间和机构；没有的写“无”。",
    "recipient_measures": "接收方的安全制度、技术措施和人员能力，可引用接收方提供的说明。",
    "legal_doc_terms": "按合同条款逐项写：保存地点和期限、到期处理、再转移限制、违约责任、争议解决等。",
    "governing_law": "写适用的法律和争议解决方式。",
    "sensitive_pi_kinds": "写出境的敏感个人信息种类，用顿号隔开；不含敏感个人信息写“不涉及”。",
    "issues": "每条一句话，一行一条。",
    "measures": "每个问题一行：措施、责任人、完成时间。",
    "verification": "写验证方式和时间。",
    "entry_type": "要办理的事项名称；可写多项。",
    "authority": "受理机构名称；不清楚可以不填，文件会按目标国法规带出。",
    "exterior_video_and_gnss": "先列数据类型，再写是否脱敏或匿名化、在哪一环做的。",
    "data_subjects": "写人群和大致人数。",
    "data_flow": "按顺序写：车端采集 → 车内存储 → 上传 → 境内平台 → 境外接收方，每一步写在哪、由谁处理。",
    "lawful_basis": "从“取得同意、合同所必需、合法利益、履行法定义务”中选，并写一句理由。",
    "consent_and_notice": "写通过什么方式告知（车机弹窗、用户协议、车身标识等），怎么取得同意；有数据保护官的写明。",
    "dpo_opinion": "写征求了谁的意见、什么时候、意见的要点。",
    "necessity": "说明不用这些个人数据为什么达不到目的，以及考虑过哪些替代办法。",
    "cross_border_mechanism": "写接收方所在国家、接收方名称，以及依据的传输工具（标准合同、认证、许可等）。",
    "supplementary_measures": "列出传输工具以外的补充措施。",
    "reevaluation": "写复评周期和触发条件。",
}

# 示例答案：一家汽车企业的写法，只供参考，不写进答案和文件
EXAMPLES = {
    "entity_nature": "企业",
    "entity_type": "中外合资",
    "registered_place": "上海市",
    "office_place": "北京市",
    "staff_count": "1200 人",
    "credit_code": "91310000XXXXXXXXXX（18 位）",
    "is_cii": "否（未收到主管部门的认定通知）",
    "pi_scale": "约 35 万人",
    "legal_rep": "张某，董事长，中国，021-6XXXXXXX，zhang@example.com，居民身份证 3101XXXXXXXXXXXXXX",
    "security_officer": "李某，首席信息安全官，中国，021-6XXXXXXX，li@example.com，居民身份证 3101XXXXXXXXXXXXXX",
    "security_org": "数据安全管理委员会，12 人",
    "agent_info": "王某，法务经理，021-6XXXXXXX，wang@example.com，居民身份证 3101XXXXXXXXXXXXXX",
    "law_compliance": "2025 年 3 月因车主 App 隐私政策未单列敏感个人信息，被市网信办责令整改，2025 年 4 月已完成整改；此外无处罚和调查。",
    "scenario_summary": "在埃及运营的网联车辆把故障码和车辆运行数据经加密专线回传上海研发中心，用于远程诊断和质量分析。",
    "purpose": "回传车辆故障日志和运行数据，用于远程诊断和召回前的质量分析",
    "data_types": "车辆运行数据、故障码、定位轨迹（上传前精度降到 1 公里）",
    "important_data": "否",
    "sensitive_pi": "是，涉及精确定位轨迹约 8000 人",
    "industry_field": "汽车制造",
    "data_volume": "今年已传 2 万人；未来三年预计约 10 万人",
    "recipient": "某汽车技术（德国）有限公司，德国，慕尼黑某路 1 号，汽车软件研发",
    "recipient_contact": "Hans Müller，数据保护官，+49 89 XXXX XXXX，dpo@example.de",
    "retention_and_blackbox": "保存 2 年；存放在接收方位于法兰克福的自有机房；到期后自动删除并留存删除记录",
    "risk_measures": "技术：传输加密、人脸和车牌脱敏、最小权限访问；管理：访问审批、操作日志审计、员工保密协议",
    "risk_rating": "风险：接收方把数据再转给第三方；可能性低，严重程度高（中风险）；措施：合同禁止再转移，我方每年审计一次；责任人：法务部；完成时限：2027-03-31\n风险：境外存储泄露；可能性中，严重程度中（中风险）；措施：传输和存储加密、访问日志审计",
    "sensitive_pi_kinds": "精确位置轨迹、车内摄像头人脸图像",
    "risk_findings": "发现 1 项：测试服务器上的诊断日志明文保存，2026 年 9 月已删除并改为加密存储",
    "risk_conclusion": "风险可控，可以传输。数据上传前已脱敏，接收方的保护措施经核查有效，合同已约定再转移限制和违约责任。",
    "assessment_work": "2026 年 8 月 1 日至 9 月 15 日，数据安全部牵头，法务部和研发中心参与，外聘第三方测评机构；通过访谈、文档审查和渗透测试开展。",
    "company_profile": "公司由某汽车集团全资控股，实际控制人为该集团；设研发、生产、销售等 12 个部门，数据安全管理委员会统筹数据安全；主营乘用车研发制造，数据资产主要是车辆运行数据和车主服务数据。",
    "storage_chain": "存放在车联网平台（境内公有云华东区域机房）；经跨境专线传到接收方的德国机房。",
    "onward_provision": "无",
    "security_mgmt": "已制定数据分类分级、数据安全事件应急预案、年度风险评估、个人信息保护等制度，设数据安全管理委员会，每季度检查一次执行情况。",
    "security_tech": "收集：车端对人脸和车牌脱敏；存储：加密存储；使用：最小权限和审批；传输：专线加 TLS 加密；删除：到期自动删除并留痕。",
    "effectiveness_proof": "2026 年 6 月通过网络安全等级保护三级测评（某测评中心）；2025 年 11 月通过 ISO 27001 认证（某认证机构）。",
    "recipient_measures": "接收方已通过 ISO 27001 认证，设专职数据保护官，访问数据须双人审批，员工每年参加数据保护培训。",
    "legal_doc_terms": "保存地点：德国法兰克福；期限 2 年，到期删除；未经我方书面同意不得再转移；接收方控制权变化时通知我方并暂停接收；违约的赔偿损失；争议提交上海国际仲裁中心仲裁。",
    "governing_law": "适用中国法律，提交上海国际仲裁中心仲裁",
    "issues": "部分测试车辆的定位轨迹未降低精度就上传\n车主 App 隐私政策没有写明数据出境的接收方",
    "measures": "定位轨迹在车端降到 1 公里精度后上传，研发中心张某负责，2026 年 12 月 31 日前完成\n隐私政策补充接收方名称和联系方式，法务部李某负责，2026 年 11 月 30 日前完成",
    "verification": "整改完成后一个月内由第三方复测，并在下一次年度审计中复查",
    "entry_type": "车辆型式批准；无线电设备型号核准",
    "authority": "国家电信管理局",
    "exterior_video_and_gnss": "车外视频、定位轨迹、车辆运行数据；车外视频在车端对人脸和车牌做模糊处理，定位轨迹上传前降低精度",
    "data_subjects": "车主约 5 万人；车外行人（未统计）",
    "data_flow": "车端采集 → 车内存储 24 小时 → 经 4G 加密上传 → 境内车联网平台（上海） → 跨境专线 → 德国研发中心，由研发团队分析",
    "lawful_basis": "取得同意：车主开通车联网服务时在车机上单独勾选同意",
    "consent_and_notice": "车机首次启动时弹窗告知并勾选同意；用户协议单列数据出境条款；车身贴标识提示摄像头在采集；已任命数据保护官",
    "dpo_opinion": "2026 年 9 月 10 日征求数据保护官和法务负责人意见：脱敏后可以传输，建议每年复评一次",
    "necessity": "远程诊断要用故障发生时的单车运行数据，汇总统计无法定位到具体车辆；考虑过只传汇总数据，但满足不了单车诊断",
    "cross_border_mechanism": "德国，某汽车技术（德国）有限公司；订立个人信息出境标准合同并备案",
    "supplementary_measures": "端到端加密，密钥由境内保管；上传前把车辆识别号替换成假名",
    "reevaluation": "每年一次；接收方或其所在国家的法规发生变化时立即复评",
}


def used_for(codes: list[str]) -> str:
    return "、".join(dict.fromkeys(DOC_SHORT.get(c, c) for c in codes))

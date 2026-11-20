# ruff: noqa: E501  提示词与模板示例是长句数据
"""Argumentative sections of the evidence documents (DPIA, LIA, TIA).

The meeting's wording for the DPIA is “a paper that clears the company”: it starts from the business
scene, argues that the data use is necessary and reasonable, then sets out the protections and the
technical means, each step resting on the one before. A form with blanks cannot do that, so these
sections are written by the model, under three rules that keep it honest:

* it may use only the facts the user gave and the clauses of the target country passed in with the
  request (numbered); it must not add a fact, and writes “【待补充】” where a fact is missing;
* every legal statement carries the numbers of the clauses it rests on; the document then prints
  the title and clause number of those clauses itself, so a citation is never typed by the model;
* a figure that appears neither in the facts nor in a cited clause is refused, and so is a
  statement that cites a clause which was not offered.

What passes is stored (``DocumentNarrative``) against a hash of everything it was written from, so
the same facts give the same text and a changed fact asks for a new one. The text is the company's
paper: it is a draft the company reads, edits and signs, never a legal opinion.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis import clause_gate, risk_items
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.core.db import Base

MISSING_FACT = "【待补充】"
MAX_CITED = 24


class DocumentNarrative(Base):
    __tablename__ = "document_narratives"
    __table_args__ = (UniqueConstraint("task_id", "code", "inputs_hash", name="uq_document_narrative_inputs"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    inputs_hash: Mapped[str] = mapped_column(String(64))
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NarrativeError(RuntimeError):
    """The model could not produce a paper that passes the checks."""


@dataclass
class Section:
    key: str
    heading: str
    brief: str  # what this part must argue
    facts: list[str]  # fact keys it may use
    flow: bool = False  # also returns the data-processing steps
    risks: bool = False  # also returns the graded risks
    field_table: bool = False  # also returns the 6-column data-item breakdown table
    dimensions: list[str] = field(default_factory=list)  # which dimensions' clauses to offer
    side: str | None = None  # "recipient" / "source": only the clauses of the country the data goes to / comes from
    role: str = ""  # exemplar role for chapter-level legal scope isolation


_EXEMPLAR_ROLE_GUIDES: dict[str, str] = {
    "governance_scope": (
        "章节定位（主体权责配置与前置审查治理）："
        "仅界定数据控制者与境外接收方的法定主体地位及指示约束边界、跨部门内控审查关口配置与立项申报留痕机制。"
        "严禁在本章罗列具体传感器字段清单、严禁展开加密算法细节，严禁提前下达全篇评估总结论。"
    ),
    "systematic_description": (
        "章节定位（端云处理触发逻辑与敏感数据定性）："
        "紧扣车载事件触发条件、源端去标识化前置关口、跨境受控传输与境外隔离存储销毁的时空边界作精炼总括，并对所涉数据是否构成法定敏感或特殊类别个人数据作出清晰定性。"
        "严禁在本章提前开展多替代方案排他比选，严禁提前下达全篇评估总结论。"
    ),
    "lawfulness_and_principles": (
        "章节定位（合法性事由要件涵摄与基本原则落地）："
        "对照数据发出国现行法规条款，剖析各细分场景所依托合法性事由（合同履行必需、单独知情同意、法定传输工具或正当利益）的法定构成要件、禁止捆绑强迫规则及分层告知撤回机制，并阐明源端默认隐私保护与最短留存期限如何落实公平、目的限定与最小化原则。"
        "严禁逐一背诵具体传感器或字段名称清单，严禁提前下达全篇评估总结论。"
    ),
    "necessity_proportionality": (
        "章节定位（反事实排他检验与相称性权衡）："
        "运用反事实排他法，对比纯车端离线处理、原始未脱敏直传、现行源端脱敏加事件触发白名单传输及远期完全属地化闭环，论证为何现行方案是在实现同等行车安全验证或故障溯源目标下对个人权利侵入性最小的选择，并明确维持相称性的刚性生效前提。"
        "严禁整串照抄字段名称清单，严禁提前下达全篇评估总结论。"
    ),
    "safeguards_lifecycle": (
        "章节定位（全生命周期安全控制与技术补充措施实效）："
        "聚焦源端去标识化、双向鉴权加密通道、发出国境内密钥独立管控、境外逻辑隔离存储、岗位最小授权与不可篡改审计、到期连同备份彻底删除的技术与管理控制链条，重点推演各项措施如何从机制上阻却未授权明文访问、中途篡改与超期滞留。"
        "严禁提前下达全篇评估总结论。"
    ),
    "risk_mitigation": (
        "章节定位（具体风险致害链路剖析与压降实效核验）："
        "严格针对企业已识别的具体风险事项，逐项剖析威胁触发节点、对自然人隐私与行踪或人身财产安全的潜在损害路径，以及离线二次质检、出境审批硬门禁、电子围栏或工单级只读权限如何切断致害因果链并将固有风险压降至可接受区间。"
        "严禁重述前文主体背景，严禁提前下达全篇终局放行结论。"
    ),
    "stakeholders_and_dpo": (
        "章节定位（数据主体跨法域权利救济与内控独立监督）："
        "一是阐明车主、乘员或受影响自然人行使知情、查阅、更正、删除、撤回同意及向监管机关申诉的境内统一受理入口与境外接收方合同协同处置时限；二是阐明数据保护官或合规负责人的独立审查意见如何转化为上线前置硬约束与偏离叫停机制。"
        "本章不展开外国公权力调取抗辩，严禁提前下达全篇评估总结论。"
    ),
    "recipient_and_remedy": (
        "章节定位（接收方法域制度评估与双边法域管辖冲突阻断）："
        "双层剖析接收方法域专门数据保护立法与独立监管体系、当地公权力机关调取数据的法定程序要件与比例原则边界；专门推演当接收国机关提出调取请求时，如何执行‘法定职权与文书完备性审查→对宽泛或越权要求依法异议→第一时间通报发出方并履行发出国法定报批前置→依托源端脱敏与独立密钥阻却明文交付’的阻断机制，并说明合同目的限制、禁止擅自再转移及连带违约责任。"
        "严禁提前下达全篇评估总结论。"
    ),
    "risk_and_verdict": (
        "章节定位（跨境风险成因研判、防控压降与全篇唯一终局结论）："
        "剖析跨境传输链路截获篡改、境外越权访问、超期留存及违规再转移等固有风险成因与防控压降实效，核定剩余风险是否处于可接受区间，并给出全篇唯一终局结论："
        "存在【待补充】或未压降高风险项时明确裁定暂缓放行；事实齐备且剩余风险可控时明确核定为‘有条件通过’，列明法定前置程序、最小白名单锁定、到期含备份销毁、单路径异常熔断与专项重评条件。"
    ),
    "conclusive_ruling": (
        "章节定位（全篇唯一终局合规裁断、放行硬门禁与动态复评规则）："
        "本章位于全篇各专项核验及风险台账之后，严禁重新铺陈第四章具体风险成因或复诵传感器字段清单。仅用一至两段精炼权威的裁断文字完成收束："
        "当存在【待补充】或未压降高风险时，明确裁定暂缓放行并指出放行前提；当事实齐备且剩余风险均处于可接受区间时，依据所引法规条款正式核定‘有条件通过’，逐一锁定法定前置许可/备案完结、最小白名单与到期含备份销毁持续有效、任一子路径异常立即暂停熔断并转入本地降级隔离运行，以及车载功能变更、规模跨越阈值、接收方调整或属地修法时强制启动专项重新评估。"
    ),
}

_DOCTYPE_STYLE_GUIDES: dict[str, str] = {
    "cn_official": (
        "文体定位（国内网信监管申报核查体）：以申报主体自查核实记录为主线，开门见山陈述已核实的制度、系统控制与合同义务，句式凝练直白，杜绝空泛铺垫。"
    ),
    "dpia": (
        "文体定位（个人基本权利影响分析与法条涵摄体）：紧扣车载业务对自然人隐私合理预期、行踪自决与人身财产安全的实质影响，重点做‘事实边界→法条要件→权利保障实效’的法理涵摄，不堆砌重复背景。"
    ),
    "lia": (
        "文体定位（合法利益三步权衡论证体）：紧扣具体安全或履约利益的正当性、无更小侵入替代手段的反事实排他检验、以及合理预期与退出权对潜在影响的对冲平衡。"
    ),
    "tia": (
        "文体定位（跨境法域对抗与调取阻断评估体）：聚焦接收国公权力调取程序边界、双边司法执法管辖冲突，以及源端去标识化与密钥境内独立控制能否在实务中实质阻却越权明文获取。"
    ),
}

_EXEMPLAR_VARIANTS: dict[str, tuple[str, ...]] = {
    "governance_scope": (
        (
            "泛化笔法示范（仅学习主体定性与内控关口密度，严禁照抄非本节事实）："
            "“本单位作为确定车载数据处理目的与方式的主体，依法承担数据控制者主体责任；境外关联或合作接收主体仅按书面指令在限定研发或运维环境内受托处理，控制者法定责任不因跨境流转而豁免或转移。"
            "在启动跨境传输前，法务合规、车云研发与信息安全团队已建立联合立项审查关口，逐项核验控制者及接收方登记信息、处理用途、前置审批要件与过往守法记录，未完成内控核签与法定申报前严禁开放生产环境跨境同步。”"
        ),
        (
            "泛化笔法示范（仅学习权责边界与留痕治理切入方式，严禁照抄非本节事实）："
            "“围绕受评车型的跨法域业务协同需求，本单位已将数据保护职责嵌入研发与运营治理架构，指定专职合规负责人统筹事前风险审查、申报材料核对与运行期监督留痕。"
            "本次评估严格划定受评车队、触发场景与参与主体角色边界，凡超出既定用途或未经前置核准的车端本地运算数据均排除在跨境流转范围之外，从治理源头锁定合规审查基准。”"
        ),
    ),
    "systematic_description": (
        (
            "泛化笔法示范（仅学习事件触发闭环与特殊类别排除笔法，严禁照抄非本节事实）："
            "“车载数据处理严格采用预设事件触发机制，仅在特定安全告警、诊断指令或授权测试工况出现时按需采集并进入受控链路，日常行驶状态下不开展后台静默全量回传。数据出境前先行在境内前置节点完成直接识别符剥离与敏感精度收敛，随后经加密专用通道传往境外隔离环境并在约定期限届满时连同备份彻底清除。"
            "从数据定性审视，出境数据包在源端已消除可直接定位特定自然人的明文特征，全程不提取用于唯一识别自然人的生物特征模板，不构成法定禁止处理的特殊类别生物识别数据；对仍具车辆或工单关联性的工程参数，全程按高敏标准实施受控隔离。”"
        ),
        (
            "泛化笔法示范（仅学习时空边界收束与敏感属性界定笔法，严禁照抄非本节事实）："
            "“整个流转链路由车端触发采集、境内前置去标识化质检、双向鉴权跨境传输、境外受控只读调阅及期满自动销毁五个刚性节点构成，各节点的物理部署地点、责任主体与留存窗口均已固化。"
            "拟跨境流转的信息严格限定于经源端脱敏的必要业务与工况参数，不含无关车内外音视频或原始生物识别信息，有效将境外侧的数据暴露面压缩在可控的工程范围内。”"
        ),
    ),
    "lawfulness_and_principles": (
        (
            "泛化笔法示范（仅学习法条要件涵摄与分层告知笔法，严禁照抄非本节事实）："
            "“对照发出国数据保护法关于处理合法性与公平透明原则的规定，本单位按业务属性拆分权利基础：核心维保或作业管理依托合同履行所必需，跨法域传输及可选交互功能则以显著分层告知后的独立明示同意及主管机关法定许可为前提，绝不将非必要数据授权与基础驾驶安全功能捆绑。"
            "在基本原则落地方面，源端默认关闭无关采集与前置脱敏直接兑现数据最小化要求，固定留存上限与期满清零机制则严格对应法定存储限制义务，确保处理活动始终收敛于已声明的特定目的之内。”"
        ),
        (
            "泛化笔法示范（仅学习法定工具适格性与权利自决论证，严禁照抄非本节事实）："
            "“本次数据处理与跨境提供均锚定于明确的法定事由及跨境保障工具，事前通过车机界面、外部公示或书面协议向相关自然人清晰披露接收方身份、处理用途、保存周期与行权方式，保障同意的知情性与自愿性。"
            "同时，本单位将目的限定、准确性核验与保密安全义务转化为前置网关过滤规则和受托处理合同条款，禁止境外接收方脱离原定用途开展二次利用，从法理与制度两端夯实合规基础。”"
        ),
    ),
    "necessity_proportionality": (
        (
            "泛化笔法示范（仅学习反事实排他检验与相称性权衡笔法，严禁照抄非本节事实）："
            "“以反事实排他标准审视替代路径：若仅依赖单车离线算力或纯合成样本，境外工程团队无法复现跨批次硬件在复杂工况下的真实耦合故障，既定安全验证与定向修复目标将落空；反之，若不经源端脱敏直接回传原始全量数据，则会无谓暴露自然人身份与行踪，明显超出必要限度。现行‘源端去标识化＋事件触发最小白名单’方案，是在达成同等行车安全目标下对个人权益侵入性最小的路径。"
            "在利益权衡上，及时消除车辆运行安全隐患兼具公共交通安全与用户切身利益价值，而源端精度收敛、严控访问人数与短期自动销毁已将隐私侵扰压至合理预期之内；相关控制持续有效及法定前置审批完结共同构成相称性成立的刚性前提。”"
        ),
        (
            "泛化笔法示范（仅学习最小必要边界推导与刚性前提锁定，严禁照抄非本节事实）："
            "“若彻底剥离车辆或事件级关联令牌，跨区域技术团队将无法把异常诊断结果定向回传至具体待修车辆或特定测试批次；而短期内在各单一海外市场重复建设全套核心算法基线库亦不具备现实工程可行性。因此，仅在异常或指定工况触发时按白名单传输去标识化参数具有客观必要性。"
            "通过将无关个人身份、连续轨迹及原始音视频挡在跨境链路之外，并辅以严格留存上限与随时撤回通道，业务安全收益与自然人基本权利保护之间实现了实质相称。”"
        ),
    ),
    "safeguards_lifecycle": (
        (
            "泛化笔法示范（仅学习技术阻断因果推演笔法，严禁照抄非本节事实）："
            "“在技术与组织保障层面，本单位围绕数据流转关键暴露点布设纵深防线：数据离开境内前先行剥离直接身份标识并将还原主密钥独立托管于境内硬件安全模块，使境外环境即便遭遇异常亦无法单方逆向还原自然人身份；跨境通信全程启用双向证书鉴权与报文完整性校验，阻断跨网窃听与重放篡改。"
            "境外存储侧实行专区分隔、实名白名单最小权限只读授权与全量不可篡改日志留痕，严禁批量导出；一旦到达约定留存期限，系统自动对生产卷及灾备副本执行彻底清除，消除长期沉淀隐患。”"
        ),
        (
            "泛化笔法示范（仅学习补充措施弥补法域落差笔法，严禁照抄非本节事实）："
            "“针对跨法域流转的外部暴露风险，源端去标识化与境内独占密钥管控构成了核心技术屏障，配合高强度传输与静态加密，确保数据在途及驻留期间维持机密性与完整性。"
            "组织管理上同步执行双因子认证、按工单审批调阅及定期权限复核，将可接触数据的人员压缩至最小范围，使各项法定安全保护义务落到可审计、可核验的工程控制点上。”"
        ),
    ),
    "risk_mitigation": (
        (
            "泛化笔法示范（仅学习逐项风险致害阻断推演，严禁照抄非本节事实）："
            "“针对车端极端光照或侧向角度可能漏检个别面部车牌、进而导致可识别画面外溢的风险，本单位在境内前置安全站增设离线高精度二次质检闸机，凡置信度异常帧一律整段丢弃，从源头阻断未脱敏影像进入跨境队列。针对未获批文前脚本误触发同步的程序违规风险，出境防火墙已部署法务数字签名硬门禁，未核验有效批文号前物理切断对外连接。"
            "针对空间点云或定位信息拼接还原敏感地理特征的隐患，车端内置敏感区域电子围栏自动停录并剥离绝对经纬度，境外隔离云则按最短周期自动清零销毁；经定向压降，各项固有中高风险均已实质降至低风险可接受区间。”"
        ),
        (
            "泛化笔法示范（仅学习触发源头控制与剩余风险核验笔法，严禁照抄非本节事实）："
            "“逐一审视已识别风险的传导路径：终端报文若混入连续定位将暴露车主出行规律，故终端默认关闭连续轨迹上报，仅在紧急告警瞬间附带经本地网关模糊处理的粗粒度区域位置；境外运维若权限过宽则易引发越权调阅，故锁定极少数实名工程师按工单双因子只读访问并留存全量审计日志。"
            "相关控制分别在采集源头削除敏感精度、在境外侧封堵非授权调阅与批量导出通道，使发生概率与影响范围同步显著下降，剩余风险完全处于受控可接受范围。”"
        ),
    ),
    "stakeholders_and_dpo": (
        (
            "泛化笔法示范（仅学习跨境权利协同响应与DPO硬约束笔法，严禁照抄非本节事实）："
            "“在跨法域流转架构下，本单位通过车机界面、隐私中心或车身显著标识公示备案信息与专属行权渠道，由发出国属地合规团队统一受理查阅、更正、删除、限制处理及撤回同意请求；核验身份后通过合同绑定的协同接口指令境外接收方同步处置并回传结果，不得以数据已出境为由拖延或拒绝，遇争议时保障数据主体依法向属地监管机关申诉。"
            "数据保护官及合规负责人的独立审查意见已转化为正式上线门禁：未经确认最小白名单锁定、脱敏质检达标及主管机关批文齐备，生产系统不得开启跨境传输；运行期间若权利响应或安全控制偏离审查要求，数据保护官有权直接指令暂停同步并启动整改。”"
        ),
        (
            "泛化笔法示范（仅学习知情自决保障与独立审查采纳落地，严禁照抄非本节事实）："
            "“围绕受影响自然人的知情权与自决权，本单位实施分层透明告知并提供随时可触达的撤回与异议入口，一旦撤回授权，前置网关立即停止后续对应数据的跨境转发，并联动境外节点按约定期限清除存量记录。"
            "内控治理上，数据保护官已对本次处理的必要性、技术隔离充分性及跨境协议约束力出具书面审查意见，业务与研发部门全额采纳并将其嵌入发布审批流，使独立合规监督具备实质否决与纠偏效力。”"
        ),
    ),
    "recipient_and_remedy": (
        (
            "泛化笔法示范（仅学习接收国公权力调取边界与双边管辖阻断推演，严禁照抄非本节事实）："
            "“经核查接收方法域现行专门数据保护法与独立监管制度，当地对数据处理安全、目的限制及向第三国再转移均设有明确法定约束；公权力机关因执法或安全事由调取数据须具备法定职权、履行正式文书程序并受必要性与比例原则限制，不存在要求商业车企无差别开放后台直连的普遍义务。"
            "针对跨国调取可能引发的双边管辖冲突，跨境协议确立了刚性阻断程序：接收方收到当地机关调取指令时，须先行审查职权与法律文书完备性，对超范围或程序瑕疵要求依法提出异议，第一时间书面通报本单位，且未经依法报请发出国主管机关批准不得擅自对外提供；叠加源端去标识化与密钥境内独管，从技术与契约双端阻却未授权明文交付。”"
        ),
        (
            "泛化笔法示范（仅学习法域保护水平核验与违约救济约束，严禁照抄非本节事实）："
            "“接收国已建立成文个人数据保护法制及行政司法救济渠道，境外接收方亦受当地监管机关执法管辖。就外部强制调取风险而言，除当地法定个案程序约束外，本单位在境内前置节点剥离直接身份标识并独占保管还原密钥，使境外存储侧不具备直接还原特定自然人身份的条件。"
            "双方签署的跨境法律文件进一步锁定唯一处理目的、严禁擅自向第三国或外部第三方再转移，明确外部调取抗辩通报义务、连带违约责任及数据主体跨境行权协同机制，确保境外保护水平不因跨境流转而减损。”"
        ),
    ),
    "risk_and_verdict": (
        (
            "泛化笔法示范（仅学习风险压降总括与双态自适应终局判定，严禁照抄非本节事实）："
            "“综合审视数据跨境流转全链路，潜在风险主要集中于跨网传输截获、境外账号越权调阅、超期滞留及非授权再转移。依托境内前置去标识化、双向鉴权加密通道、境外最小权限只读审计、期满连同备份彻底删除及跨境合同阻断条款，原有中高固有风险均已压降至低风险可接受区间。"
            "（事实齐备时）：据此核定本次跨境数据处理目的正当、范围最小必要、整体剩余风险可控，在依法办结发出国法定申报备案或事先授权程序、维持单路径异常暂停熔断及触发条件重评的前提下，予以有条件通过。（含【待补充】时）：鉴于【待补充】等关键事项尚待核实固化，现阶段裁定暂缓放行，须待缺失项补齐并复核通过后方可启动传输。”"
        ),
        (
            "泛化笔法示范（仅学习固有至剩余风险闭环与刚性放行前提，严禁照抄非本节事实）："
            "“对照法定出境安全审查要件，本单位已通过源端字段收敛、高强度传输存储加密、境内密钥独管及违约追责条款封堵主要泄露与滥用通道，已识别风险事项均已完成压降闭环。"
            "（事实齐备时）：经全面评估，拟开展的跨境传输能够持续维持符合法定标准的保护水平，核定为有条件通过，正式投产前须完结全部法定前置手续并按期开展合规审计与动态复评。（含【待补充】时）：由于【待补充】要素尚未闭环，当前不具备放行条件，须待补齐核验后重新审定。”"
        ),
    ),
    "conclusive_ruling": (
        (
            "泛化笔法示范（仅学习终局凝练裁断与硬门禁锁定，严禁重述第四章具体风险链条）："
            "“（事实齐备时）：综合前文合法性要件涵摄、最小侵害排他比选及量化风险核验结果，本次汽车数据跨境处理具备充分法理正当性与技术保障实效，全部剩余风险均已压降至可接受低位区间，正式裁定为‘有条件通过’。生产环境放行须持续满足三项刚性门禁：一是依法取得发出国主管机关事先授权或完结法定备案申报程序，未获生效凭证前严禁开启同步；二是严格锁定最小必要字段白名单并落实期满连同备份副本彻底销毁；三是实行单路径熔断机制，任一子场景出现脱敏异常或安全告警时立即单独切断该路径跨境传输并转入本地降级隔离运行。凡遇车载采集功能扩容、数据规模跨越法定阈值、接收方变更或属地修法，须立即启动专项重新评估。（含【待补充】时）：鉴于当前在【待补充】等核心合规要素上尚未完成核实闭环，正式裁定‘暂缓放行’，在补齐全部缺失信息并通过复审前不得启动跨境传输。”"
        ),
        (
            "泛化笔法示范（仅学习实质同等保护裁定与动态复评闭环，严禁重复铺陈前文细节）："
            "“（事实齐备时）：经全篇核查比对，在法定跨境传输保障工具与源端去标识化、境内独立密钥控制、最小权限审计等补充措施共同约束下，拟跨境数据在接收方能够获得与发出国法律要求实质相当的保护水平，现予以‘有条件通过’。本裁定以办结法定前置行政程序、签署具连带约束力的跨境协议、落实到期含备份安全删除及异常即时熔断回退为持续生效前提；运行期间按计划执行定期合规审计，一旦业务范围、接收方法律环境或安全态势发生重大变化，即刻暂停相关链路并重新履行影响评估。（含【待补充】时）：因【待补充】事项仍待确认，现阶段不予放行，须待整改补齐后另行核定。”"
        ),
    ),
}

_EXEMPLAR_FEW_SHOTS: dict[str, str] = {k: v[0] for k, v in _EXEMPLAR_VARIANTS.items()}


def _resolve_doc_style(context: dict, section: Section) -> str:
    doc_code = str(context.get("doc_code") or "").lower()
    src = str(context.get("source_country") or "").strip()
    if doc_code == "tia":
        return "tia"
    if doc_code == "lia":
        return "lia"
    if src in ("中国", "CN") and doc_code in ("risk_report", "dpia", ""):
        return "cn_official"
    if doc_code == "dpia":
        return "dpia"
    if "平衡测试" in (section.heading or "") or "合法利益" in (section.heading or ""):
        return "lia"
    if "补充保护措施" in (section.heading or "") or "接收国法律" in (section.heading or ""):
        return "tia"
    return "dpia"


def _pick_exemplar(role: str, style: str, section: Section, context: dict) -> str:
    variants = _EXEMPLAR_VARIANTS.get(role) or (_EXEMPLAR_FEW_SHOTS[role],)
    seed = f"{style}:{section.key}:{context.get('source_country', '')}:{context.get('recipient_country', '')}"
    idx = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16) % len(variants)
    return variants[idx]


def _resolve_role(section: Section) -> str:
    if section.role in _EXEMPLAR_ROLE_GUIDES:
        return section.role
    k = (section.key or "").lower()
    h = section.heading or ""
    if "conclusion" in k or any(w in h for w in ("结论", "综合判定", "放行决定")):
        return "conclusive_ruling" if k == "conclusion" else "risk_and_verdict"
    if "stakeholder" in k or "审查意见" in h:
        return "stakeholders_and_dpo"
    if k == "risk" or "量化管理" in h:
        return "risk_mitigation"
    if any(x in k for x in ("recipient", "law", "balance")) or any(
        w in h for w in ("接收方", "接收国", "平衡测试")
    ):
        return "recipient_and_remedy"
    if any(x in k for x in ("security", "measures", "review")) or any(
        w in h for w in ("技术防护", "补充保护措施", "复评")
    ):
        return "safeguards_lifecycle"
    if any(x in k for x in ("legal_basis", "tool")):
        return "lawfulness_and_principles"
    if "systematic_flow" in k or "系统性描述" in h:
        return "systematic_description"
    if any(x in k for x in ("necessity", "business")) or "必要性" in h:
        return "necessity_proportionality"
    return "governance_scope"


@dataclass
class Paper:
    blocks: list[dict] = field(default_factory=list)
    tokens: int = 0
    dropped: list[str] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)  # ids of the clauses the paper cites (its table at the end)


def narrative_sections(definition: dict) -> list[Section]:
    return [Section(**s) for s in definition.get("narrative", [])]


def inputs_hash(definition: dict, values: dict[str, str], citations: list[dict]) -> str:
    """Everything the paper is written from: the template, the facts, the clauses on offer."""
    payload = {
        "narrative": definition.get("narrative", []),
        "title": definition.get("title"),
        "values": {k: values.get(k, "") for k in sorted(values)},
        "clauses": sorted(
            f"{c.get('side', '')}:{c.get('clause_id') or c.get('citation_tag')}" for c in citations
        ),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


OFFER_PER_SECTION = 7
OFFER_CHARS = 700


_NARROW_RESEARCH_CLAUSE_RE = re.compile(
    r"(?<!除非涉及)(?:为|用于|在|专门用于|从事)(?:科学|历史|统计)研究(?:目的|领域|场景|时|的)?|研究例外|科学研究结果|recherche scientifique|scientific research",
    re.I,
)
_NARROW_MEDICAL_CLAUSE_RE = re.compile(
    r"医疗机构|医护机构|行医|临床试验|患者.*个人数据|病历|自愿绝育|医疗服务法|人类生物医学",
    re.I,
)
_NARROW_PRESS_CLAUSE_RE = re.compile(
    r"广播和电视|新闻报道|文学艺术创作",
    re.I,
)
_NARROW_BUILDING_CCTV_RE = re.compile(
    r"工作场所和共同私人场所安装视频监控|生物识别门禁设备",
    re.I,
)
_GENERAL_SENSITIVE_LIST_RE = re.compile(r"种族|民族|政治观点|遗传数据|生物识别")


def _is_mismatched_narrow_clause(c: dict, context_blob: str) -> bool:
    """Exclude narrow special-sector exemption clauses (e.g. academic scientific research, hospitals,
    broadcasting, workplace building CCTV/turnstiles) when the project's actual business facts do not involve those sectors."""
    title_and_text = f"{c.get('document_title') or ''} {c.get('quote_zh') or ''} {c.get('quote_original') or ''}"
    if _NARROW_BUILDING_CCTV_RE.search(title_and_text) and not re.search(
        r"工作场所|园区监控|门禁设备|考勤门禁", context_blob
    ):
        return True
    if _GENERAL_SENSITIVE_LIST_RE.search(title_and_text):
        return False
    if _NARROW_RESEARCH_CLAUSE_RE.search(title_and_text) and not re.search(
        r"科学研究|学术研究|历史研究|统计调查|临床研究|高等院校|科研院所", context_blob
    ):
        return True
    if _NARROW_MEDICAL_CLAUSE_RE.search(title_and_text) and not re.search(
        r"医疗|医院|临床|患者|病历|救护", context_blob
    ):
        return True
    if _NARROW_PRESS_CLAUSE_RE.search(title_and_text) and not re.search(
        r"新闻|广播|电视|媒体|出版", context_blob
    ):
        return True
    return False


def _select_section_pool(
    citations: list[dict],
    dimensions: list[str] | None = None,
    side: str | None = None,
    context_blob: str = "",
) -> list[dict]:
    pool = [c for c in citations if not dimensions or c.get("dimension_code") in dimensions]
    if side:
        pool = [c for c in pool if c.get("side") == side]
    if context_blob:
        filtered = [c for c in pool if not _is_mismatched_narrow_clause(c, context_blob)]
        if filtered:
            pool = filtered
    return pool


def _offered(
    citations: list[dict],
    dimensions: list[str] | None = None,
    side: str | None = None,
    context_blob: str = "",
) -> list[dict]:
    """The numbered clauses the model may rest a statement on: the clauses found for the
    dimensions this section is about, at most ``OFFER_PER_SECTION`` of them."""
    pool = _select_section_pool(citations, dimensions, side, context_blob)
    out = []
    for c in pool[:OFFER_PER_SECTION]:
        text = str(c.get("quote_zh") or "").strip()
        if text in ("", "(未提供译文)", "（未提供译文）"):
            text = str(c.get("quote_original") or "").strip()
        out.append(
            {
                "n": len(out) + 1,
                "document": str(c.get("document_title", "")),
                "clause": str(c.get("clause_number", "")),
                "text": text[:OFFER_CHARS],
            }
        )
    return out


SYSTEM = (
    "你是资深汽车数据跨境合规律师与企业首席合规官的撰写助手，替企业以第一人称申报主体视角撰写正式提交监管部门审查或存档备查的数据合规评估报告章节。"
    "只能使用“facts”与“context”里已给出的企业业务事实及“clauses”里编号的条文；不得补充任何未给出的事实、数字、日期或机构名称，严禁编造任何未在 facts、context 或所引条文中出现的数字（包括年份、百分比、字节数、密钥位数、天数、人数、条号等）。"
    "仅当本节“facts”里某一项的值明确为“【待补充】”时，在用到它的地方原样写“【待补充】”，不要编、不要猜，但围绕该事项的合规逻辑框架与法理分析照样完整展开；若本节“facts”各项均已填写具体内容（无“【待补充】”），正文中严禁出现任何“【待补充】”字样；facts 里没有列出的事项不属于缺失项，绝不可写成“【待补充】”。"
    "每个涉及法律要求的论断，必须在 cites 里写出所依据条文的编号（clauses 里的 n）；没有条文依据的论断不得写成法律要求。引用具体法条条号（如“第X条”）时只能引用本节 clauses 中实际给出的条号，不得照搬 facts 或 context 文本里顺带提及的其他法条条号；clauses 为空时不写具体条号且不因 clauses 为空输出“【待补充】”。"
    "商业车企开展的自动驾驶路测、感知算法验证、远程故障诊断与车联网运维属于商业产品研发与安全履约活动，不属于法律上的‘科学研究、历史研究或统计调查’特别豁免范畴；严禁将针对学术科研或历史统计的特别豁免规则套用于商业车载业务（若所给一般原则条文末尾顺带提及‘除非用于历史或科学研究’，仅可援引该条前半部分的诚信收集与目的限制一般规则）。"
    "当 context 带有 legit_basis 时，发出国法律是否承认合法利益只能严格照 context.legit_basis 陈述；当 context 带有 route 时，结合 context.route 已研判的法定出境路径论证；当 context 带有 recipient_law 且已列明接收国数据保护法、监管机构、公权力调取依据与救济途径时，须直接运用这些要素深入分析，严禁在要素齐备时输出“【待补充】”，且接收国关于再传出第三国的规定不得误判为本次传入缺少许可。"
    "文风须达到头部律所正式递交监管机关的法律意见书水准：全程采用企业已完成核查、已落实控制的正式陈述语气（严禁写成“应围绕……开展评估”“建议企业……”等顾问指导口吻）。"
    "严格执行‘表主文辅、章节职责隔离与去机械化’硬约束（参见 style_guide、role_guide、sibling_chapters 与前序章节提示）："
    "（1）篇幅精炼高密：常规章节写两段逻辑严密、直击要件的法理论述（每段约一百四十至二百一十字）；终局裁断章（conclusive_ruling）写一至两段凝练权威的审批裁断（每段约一百四十至二百字），严禁为凑字数反复铺陈同一背景；"
    "（2）严禁跨章‘报菜名’复读：除专门拆解数据范围的章节外，其余章节严禁把三项及以上具体传感器或数据项名称用顿号一字排开逐一背诵，只点名与本段论点直接相关的单一字段，或用贴合本项目实际业务的统称概括（严禁在不含摄像头或雷达的纯诊断/售后项目中凭空写入‘感知片段’等非本项目术语；提及具体单项名称时与 context 中标准数据项名称保持一致）；"
    "（3）严禁机械套路句式：严禁开头套用“在组织管理层面……在技术防护层面……”“一方面……另一方面……”等排比八股句式，严禁段末反复套用“该安排与某某条相衔接”“相互衔接”“前后衔接”“形成闭环”等机械口头禅，须交替采用法条要件涵摄、因果链阻断推演、反事实对比及权利义务边界界定等真实律师笔法。"
    "全篇只在唯一结论章（allow_final_verdict 为 true）给出‘整体剩余风险可控、依法履行前置程序后有条件通过（或暂缓放行）’的终局结论；allow_final_verdict 为 false 时严禁提前下达全篇总结论。"
    "用词必须平实规范，严禁使用生造黑话（严禁出现“最小必要裁剪”“字段裁剪”“脱敏裁剪”“精度裁剪”“去标识化裁剪”“精度降维”“地理围栏泛化”“风险缓释”“缓释闭环”“穿透约束”“硬擦除”“覆写擦除”“冷备快照”“风险机理”“技术机理”“单向哈希消息认证码假名化”“互联网协议安全隧道”等词，统一使用“遵循最小必要原则”“仅传输必要字段”“车端脱敏处理”“降低定位精度”“降低安全风险”“落实闭环管控”“合同连带约束”“到期安全删除（含全部备份数据）”“加密专用通道”）。"
    "严禁出现“本节”“下文”“读者”“所给条文”“给出的条文”“表述为”“写入方案”“下表”“如下表”“见表”“未规定”“没有规定”等写作过程用语或断言某国法律未规定某事。"
    "正文里不得出现“条文6”“第6号条文”“facts”“clauses”“cites”“context”这类内部编号或字段名，条文编号只写在 cites 里；正文纯用规范中文，严禁夹杂 facts、context 和 clauses 之外的小写英文单词。"
    "引用法规条款时，同一段落首次提及某部法规须加中文书名号并紧跟条号（如‘依《个人数据保护法》第24条’），同一段落内再次引用同一部法规时改称‘该法第X条’（或‘该条例第X条’‘该法令第X条’），避免单段内反复堆砌法规全称；条号严格照 clauses 里的 clause 写，不写外文全名。"
    "当 schema 含有 field_items 时，依据 facts 将拟出境数据拆解为具体的数据项清单行（填明名称、采集场景与内容描述、出境必要性及最小范围说明、非数字型字段形态示例、脱敏加密或权限备注，不得编造数字）。"
    "只输出 JSON，格式严格遵循 schema。"
)


_JARGON_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("最小必要范围裁剪", "最小必要范围控制"),
    ("最小必要裁剪", "最小必要范围控制"),
    ("最小化裁剪", "最小必要控制"),
    ("不可逆脱敏裁剪", "车端脱敏处理"),
    ("车端边缘脱敏裁剪", "车端脱敏处理"),
    ("边缘脱敏裁剪", "车端脱敏处理"),
    ("去标识化裁剪", "去标识化处理"),
    ("脱敏裁剪", "脱敏处理"),
    ("车端预处理裁剪", "车端预处理"),
    ("预处理裁剪", "车端预处理"),
    ("字段级裁剪", "按需筛选必要字段"),
    ("数据字段裁剪", "按需筛选必要字段"),
    ("字段裁剪", "必要字段筛选"),
    ("精度裁剪", "降低定位精度"),
    ("精度降维", "降低定位精度"),
    ("围栏裁剪", "区域化模糊处理"),
    ("地理围栏脱敏", "区域化模糊处理"),
    ("地理围栏泛化", "区域化模糊位置"),
    ("位置坐标泛化半径", "区域化模糊位置范围"),
    ("未经泛化的高频精确", "未经模糊处理的高频精确"),
    ("未泛化", "未做模糊处理"),
    ("泛化半径", "模糊位置范围"),
    ("坐标泛化", "位置模糊处理"),
    ("位置泛化", "位置模糊处理"),
    ("轨迹泛化", "轨迹模糊处理"),
    ("泛化调取要求", "宽泛无针对性的调取要求"),
    ("泛化调取", "宽泛调取"),
    ("泛化", "模糊处理"),
    ("模糊处理调取", "宽泛调取"),
    ("必要性裁剪", "必要性控制"),
    ("裁剪非必要字段", "剔除非必要字段"),
    ("裁剪无关字段", "剔除无关字段"),
    ("缓释闭环效能", "风险防控实效"),
    ("缓释闭环", "闭环管控"),
    ("风险缓释体系", "风险防控体系"),
    ("风险缓释", "风险防控"),
    ("缓释控制措施", "风险防控措施"),
    ("缓释控制", "风险防控"),
    ("缓释措施", "防控措施"),
    ("已缓释至", "已控制在"),
    ("缓释至", "控制在"),
    ("穿透式约束", "连带合同约束"),
    ("穿透约束", "连带合同约束"),
    ("含备份数据及冷备快照", "含全部备份数据"),
    ("含备份数据与冷备快照", "含全部备份数据"),
    ("备份数据及冷备快照", "全部备份数据"),
    ("备份数据与冷备快照", "全部备份数据"),
    ("及冷备快照", "及备份副本"),
    ("与冷备快照", "与备份副本"),
    ("冷备快照", "备份数据"),
    ("不可逆硬擦除", "安全彻底删除"),
    ("硬擦除", "安全彻底删除"),
    ("覆写擦除", "安全彻底删除"),
    ("单向哈希消息认证码假名化", "单向加密转换与去标识化"),
    ("互联网协议安全隧道", "加密专用传输通道"),
    ("智能合理论证", "必要性与合理性说明"),
    ("三维风险矩阵定级", "多维风险分级评估"),
    ("二维风险矩阵", "风险等级判定表"),
    ("量化评估矩阵", "风险等级判定标准"),
    ("风险矩阵", "风险等级判定表"),
    ("严谨自证评估", "系统性合规评估"),
    ("自证评估", "合规评估"),
    ("合规自证", "合规评估"),
    ("风险机理", "风险成因"),
    ("触发机理", "触发成因"),
    ("技术机理", "技术路径"),
    ("采集机理", "采集方式"),
    ("业务机理", "业务场景"),
)

_CJK_COMMA_RE = re.compile(r"(?<=[\u4e00-\u9fff]),(?=[\u4e00-\u9fff])")
_CJK_SEMI_RE = re.compile(r"(?<=[\u4e00-\u9fff]);(?=[\u4e00-\u9fff])")
_CJK_COLON_RE = re.compile(r"(?<=[\u4e00-\u9fff]):(?=[\u4e00-\u9fff])")
_CJK_PUNCT_SPACE_RE = re.compile(r"[ \t]*([，。；：])[ \t]*")
_CJK_PAREN_RE = re.compile(r"\(([\u4e00-\u9fff、，。；：0-9\s]{1,36})\)")
_PREMATURE_VERDICT_RE = re.compile(
    r"(?:综合(?:核查|评估|研判)|综上所述)[，,][^。！？\n]*?"
    r"(?:整体风险(?:处于)?可控|实质(?:同等|相当)的?保护水平)[^。！？\n]*?"
    r"(?:可(?:有序)?开展跨境传输|可正式启动跨境传输)。"
)


def _strip_premature_verdict(text: str) -> str:
    if not text or not _PREMATURE_VERDICT_RE.search(text):
        return text
    cleaned = _PREMATURE_VERDICT_RE.sub("", text).strip()
    return cleaned if len(cleaned) >= 24 else text


def polish_legal_prose(text: str) -> str:
    """Deterministically normalize obscure engineering jargon and CJK-adjacent ASCII punctuation without touching numbers or citations."""
    if not text:
        return text
    out = clause_gate.clean_margin_numbers(text)
    out = re.sub(r"([未已待])填填写项", r"\1填项", out)
    for bad, good in _JARGON_REPLACEMENTS:
        if bad in out:
            out = out.replace(bad, good)
    out = _CJK_COMMA_RE.sub("，", out)
    out = _CJK_SEMI_RE.sub("；", out)
    out = _CJK_COLON_RE.sub("：", out)
    out = _CJK_PUNCT_SPACE_RE.sub(r"\1", out)
    out = _CJK_PAREN_RE.sub(
        lambda m: f"（{m.group(1).strip()}）" if re.search(r"[\u4e00-\u9fff]", m.group(1)) else m.group(0),
        out,
    )
    out = re.sub(r"。；", "；", out)
    out = re.sub(r"[；，]。", "。", out)
    out = re.sub(r"([，。；！？])\1+", r"\1", out)
    return out


_UNIVERSAL_STATUTE_TITLES: tuple[str, ...] = (
    "汽车数据安全管理若干规定（试行）",
    "数据出境安全评估办法",
    "个人信息出境标准合同办法",
    "促进和规范数据跨境流动规定",
    "个人数据保护基本法",
    "个人数据保护法",
    "个人信息保护法",
    "数据安全法",
    "网络安全法",
    "通用数据保护条例",
    "电子交易与个人数据法",
)

_COUNTRY_PREFIX_BEFORE_LAW_RE = re.compile(r"(?:中国|新加坡|摩洛哥|埃及|突尼斯|黎巴嫩|欧盟|法国|德国|英国)$")


def _law_pronoun(law_title: str) -> str:
    base = re.sub(r"[（(][^）)]*[）)]", "", law_title).strip()
    for suffix, pron in (
        ("条例", "该条例"),
        ("法令", "该法令"),
        ("办法", "该办法"),
        ("规定", "该规定"),
        ("指南", "该指南"),
        ("决议", "该决议"),
        ("法典", "该法典"),
        ("法", "该法"),
    ):
        if base.endswith(suffix):
            return pron
    return ""


def _normalize_statute_mentions(text: str, offered_docs: list[str] | None = None) -> str:
    """Wrap bare statute names before article citations in 《》 and replace consecutive identical
    statute mentions within the same paragraph with idiomatic legal anaphora (e.g. 该法第X条)."""
    if not text:
        return text
    alias_to_canon: dict[str, str] = {}
    base_counts: dict[str, int] = {}
    clean_docs = list(dict.fromkeys(str(d).strip().strip("《》") for d in (offered_docs or []) if str(d or "").strip()))
    for d in clean_docs:
        base = re.sub(r"[（(][^）)]*[）)]", "", d).strip()
        if len(base) >= 5:
            base_counts[base] = base_counts.get(base, 0) + 1
    for d in clean_docs:
        if len(d) >= 4:
            alias_to_canon[d] = d
        base = re.sub(r"[（(][^）)]*[）)]", "", d).strip()
        if (
            len(base) >= 5
            and base_counts.get(base) == 1
            and re.search(r"(?:法|条例|办法|规定|法令|守则|指南|决议|法典)$", base)
        ):
            alias_to_canon.setdefault(base, d)
        m_yr = re.search(r"（(\d{4}年第\d+号)）", d)
        if m_yr:
            alias_to_canon.setdefault(f"{m_yr.group(1)}法", d)
            alias_to_canon.setdefault(f"第{m_yr.group(1)}法", d)
        for num, kind in re.findall(r"第(\d{1,4}-\d{1,4}(?:-\d{1,4})?)号(法律|法令|法)", d):
            if f"第{num}号{kind}" in d and d.endswith(kind):
                alias_to_canon.setdefault(f"第{num}号{kind}", d)
            elif f"（第{num}号{kind}）" in d:
                alias_to_canon.setdefault(f"第{num}号{kind}", d)
                if kind == "法律":
                    alias_to_canon.setdefault(f"第{num}号法", d)
    for univ in _UNIVERSAL_STATUTE_TITLES:
        alias_to_canon.setdefault(univ, univ)

    out = text
    for alias, canon in sorted(alias_to_canon.items(), key=lambda kv: -len(kv[0])):
        parts = re.split(r"(《[^》]+》)", out)
        pat = re.compile(
            rf"(?<![《A-Za-z0-9]){re.escape(alias)}(?=\s*第\s*[0-9一二三四五六七八九十百零]+(?:\.\d+)*\s*(?:条|款|项|段|节)|(?:关于|所列|所定))"
        )
        changed = False
        for k in range(0, len(parts), 2):
            new_seg = pat.sub(f"《{canon}》", parts[k])
            if new_seg != parts[k]:
                parts[k] = new_seg
                changed = True
        if changed:
            out = "".join(parts)

    # 统一《简称》为本节提供的规范全称
    for alias, canon in sorted(alias_to_canon.items(), key=lambda kv: -len(kv[0])):
        if alias != canon and f"《{alias}》" in out:
            out = out.replace(f"《{alias}》", f"《{canon}》")

    # 同一段落内连续引用同一部法规时，第二次起改称“该法第X条”（中间插入其他《法规》时重新写出全称，杜绝指代歧义）
    parts = re.split(r"(《[^》]+》)", out)
    last_law: str | None = None
    for idx in range(1, len(parts), 2):
        law = parts[idx][1:-1]
        prev_seg = parts[idx - 1]
        next_seg = parts[idx + 1] if idx + 1 < len(parts) else ""
        pron = _law_pronoun(law)
        if (
            last_law == law
            and pron
            and re.match(r"^\s*第\s*[0-9一二三四五六七八九十百零]+(?:\.\d+)*\s*(?:条|款|项|段|节)", next_seg)
            and not _COUNTRY_PREFIX_BEFORE_LAW_RE.search(prev_seg)
            and not prev_seg.endswith(("该", "本"))
        ):
            parts[idx] = pron
        else:
            last_law = law
    return "".join(parts)


def _schema(section: Section) -> dict:
    schema: dict[str, Any] = {"paragraphs": [{"text": "一段完整深入的合规自证论述", "cites": [1, 2]}]}
    if section.flow:
        schema["flow"] = [
            {"stage": "环节名称", "data": "该环节处理的数据", "where_who": "在哪里、由谁处理", "safeguard": "该环节的保护措施"}
        ]
    if section.field_table:
        schema["field_items"] = [
            {
                "name": "数据项名称",
                "desc": "内容描述与车载采集场景",
                "necessity": "出境必要性与最小必要范围说明",
                "example": "字段形态示例（不写虚构数字）",
                "remark": "脱敏加密与访问权限说明",
            }
        ]
    return schema


_UNITS = {"百万": 1_000_000, "千万": 10_000_000, "万亿": 1_000_000_000_000, "万": 10_000, "千": 1_000, "百": 100, "亿": 100_000_000}
# 数字后面跟的“万、千、百万、亿”是量级（“100 万”= 1000000）；“千米”“百分”之类不是
_NUMBER = re.compile(r"(\d{1,3}(?:[.,\u00a0 '\u066c]\d{3})+(?!\d)|\d+(?:[.,]\d+)?)[ \t]*(百万|千万|万亿|[万千百亿](?![米克瓦赫字分]))?")


def _numbers(text: str) -> set[str]:
    """The figures in a text as canonical values, so “20.000”, “20,000”, “2万”, “٢٠٠٠٠” all read 20000.
    A figure the model writes must be one of the figures in the facts or the cited clauses;
    writing it in another notation is fine, writing a different figure is not. A figure with a
    magnitude after it counts only as its full value: “100 万” is 1000000, not also 100."""
    from app.analysis.number_words import EASTERN_DIGITS

    out: set[str] = set()
    for raw, unit in _NUMBER.findall((text or "").translate(EASTERN_DIGITS)):
        digits = re.sub(r"[\u00a0 '\u066c]", "", raw)
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", digits):
            value = float(re.sub(r"[.,]", "", digits))
        else:
            try:
                value = float(digits.replace(",", "."))
            except ValueError:  # 表格里几列数字粘在一起（“53.000109.000140.000”）：不是一个数，跳过
                continue
        out.add(_canon(value * _UNITS[unit]) if unit else _canon(value))
    return out


def source_numbers(text: str) -> set[str]:
    """The figures a law states, however it writes them: digits, digits with a magnitude word
    (“1,5 million”), numbers in words (English, French, Arabic) and Chinese numerals in a
    translation (“一百万”“九十日”). Only for the text a statement may rest on."""
    from app.analysis.number_words import chinese_figures, word_figures

    return _numbers(text) | {_canon(v) for v in word_figures(text) | chinese_figures(text)}


_OFFICIAL_NUMBERS: dict[str, str] = {}  # 法规编号 → 官方编号（如“52-05”），每个进程查一次


def official_numbers(document_ids: list[str]) -> str:
    """The official numbers of these laws (“52-05”, “2020年第151号”): a statement may name the law
    by its number even where the clause text and the title do not repeat it."""
    return " ".join(_OFFICIAL_NUMBERS.get(str(d), "") for d in document_ids)


async def load_official_numbers(session, document_ids) -> None:
    from sqlalchemy import select

    from app.ingestion.models import DocumentVersion

    want = {str(d) for d in document_ids if d} - _OFFICIAL_NUMBERS.keys()
    if not want:
        return
    rows = (await session.execute(select(DocumentVersion.document_id, DocumentVersion.official_number).where(DocumentVersion.document_id.in_(want)))).all()
    for d, n in rows:
        if n:
            _OFFICIAL_NUMBERS[str(d)] = (_OFFICIAL_NUMBERS.get(str(d), "") + " " + n).strip()
    for d in want:
        _OFFICIAL_NUMBERS.setdefault(d, "")


def offered_numbers(citations: list[dict], offered_list: list[dict]) -> set[str]:
    """The figures of the clauses offered to the model, read from the translation and the original
    of each, and the official number of its law (``_offered`` takes the citations in order, so the
    n-th offered is the n-th citation)."""
    out: set[str] = set()
    for c, o in zip(citations, offered_list, strict=False):
        out |= source_numbers(" ".join([o["text"], str(c.get("quote_original") or ""), str(c.get("quote_zh") or ""), o["clause"], o["document"], official_numbers([c.get("document_id")])]))
    return out


def _spoken(value: str, text: str) -> str:
    """去掉原因里指明是哪个说法：在句子里找到写出这个数的那几个字（“100万埃及镑”“第65条”），找不到就写数本身。"""
    for m in _NUMBER.finditer(text or ""):
        raw, unit = m.group(1), m.group(2)
        digits = re.sub(r"[  '٬]", "", raw)
        v = float(re.sub(r"[.,]", "", digits)) if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", digits) else float(digits.replace(",", "."))
        if _canon(v * _UNITS[unit] if unit else v) == value:
            start, end = m.start(), m.end()
            before = text[max(0, start - 1):start]
            after = re.match(r"[^\s，。；、,.;:：（）()第]{0,4}", text[end:]).group(0)
            return (before if before in "第约" else "") + text[start:end].strip() + after
    return value


def _canon(value: float) -> str:
    return str(int(value)) if value == int(value) else str(round(value, 4))


def _check_paragraph(
    item: Any, offered: dict[int, dict], allowed_numbers: set[str], allowed_words: set[str] | None = None
) -> tuple[dict | None, str]:
    if not isinstance(item, dict):
        return None, "格式不对"
    text = str(item.get("text") or "").strip()
    if not text:
        return None, "空段落"
    if re.search(r"条文\s*\d|第\s*\d+\s*号条文|\bfacts?\b|\bclauses?\b|\bcites?\b", text, re.I):
        return None, "正文里出现内部编号或字段名"
    cites = [c for c in (item.get("cites") or []) if isinstance(c, int)]
    bad = [c for c in cites if c not in offered]
    if bad:
        return None, "引用了没有提供给它的条文（编号 " + "、".join(map(str, bad)) + "）"
    if _PROCESS_WORDS.search(text):
        return None, "正文里出现写作过程的用语（" + _PROCESS_WORDS.search(text).group(0) + "）"
    if _NOT_PROVIDED.search(text):
        return None, "断言该国“未规定”某事：只能写条文规定了什么，没写到的写成建议做法"
    wrong = _article_mismatch(text, [offered[c] for c in cites])
    if wrong:
        return None, f"正文写的“{wrong}”不是所引条文，也不在所引条文里"
    if len(cites) == 1:
        from app.analysis import doc_rules

        c_text = str(offered[cites[0]].get("text") or "")
        c_clean = re.sub(r"\s+", "", c_text)
        if doc_rules._ALL.search(c_clean) and not doc_rules.connective(c_text) and re.search(r"任选其一|满足其中一项即可|符合其中任一|任一条件即可", text):
            return None, "所引条文要求同时满足各项条件，正文写成了任选其一"
        if doc_rules.connective(c_text) and not doc_rules._ALL.search(c_clean) and re.search(r"(下列|以下)(条件|情形)之一|满足其中任一|任一条件", c_clean) and re.search(r"须同时满足|必须同时满足|应同时满足|须全部满足", text):
            return None, "所引条文列举的是任选其一的情形，正文写成了须同时满足"
    # 日期按“年、月、日”整体和所引条文对（条文写“14 avril 2001”“27 جويلية 2004”也认），不拆成单个数字
    from app.analysis.number_words import source_dates, source_months, written_dates

    dates, rest = written_dates(text)
    if dates:
        source = " ".join(" ".join(str(c.get(k) or "") for k in ("text", "original", "clause", "document")) for c in offered.values())
        have_d, have_m = source_dates(source), source_months(source)
        # “自某日起 X 年”算出的截止日：起算日在条文里、X 年也在条文或句子里写着，算出来的日子也认
        spans = {int(x) for x in re.findall(r"(\d+)\s*年(?!\d)", source + text)} | {
            n for n in (_zh_number(x) for x in re.findall(r"([一二两三四五六七八九十]+)\s*年", source + text)) if n
        }
        derived = {(y + n, mo, d) for (y, mo, d) in have_d for n in spans if 0 < n <= 30}
        missing = [w for w, d in dates if (d not in have_d and d not in derived if len(d) == 3 else d not in have_m)]
        if missing:
            return None, "、".join(f"“{w}”" for w in missing[:3]) + "在所引条文里没找到"
    stray = _numbers(rest) - allowed_numbers
    if stray:
        shown = [_spoken(x, rest) for x in sorted(stray, key=lambda x: float(x))[:3]]
        return None, "、".join(f"“{w}”" for w in shown) + "在所引条文里没找到"
    if allowed_words is not None:
        # 中文论述里夹了英文词（如“likewise”）：事实和条文里没有的小写英文词不能出现；人名、机构名、缩写不受影响
        leaked = set(re.findall(r"(?<![A-Za-z])[a-z]{4,}(?![A-Za-z])", text)) - allowed_words
        if leaked:
            return None, "正文里夹了英文词：" + "、".join(sorted(leaked)[:3])
    return {"text": text, "cites": sorted(set(cites))}, ""


def _clause_label(clause: str) -> str:
    """“43”/“十一”/“٤٣”/“premier” → “第43条”/“第十一条”/“第43条”/“第1条”；已带单位的标签原样。"""
    from app.knowledge.reading import article_label

    c = (clause or "").strip().translate(_INDIC)
    if re.fullmatch(r"[0-9]+[A-Za-z]?|[一二三四五六七八九十百零]+", c):
        return f"第{c}条"
    return article_label(c) or c


_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# 写作过程用语、提示词漏进正文
_PROCESS_WORDS = re.compile(r"所给条文|所提供的条文|给出的条文|表述为|写入方案|读者|本简报|本部分将|下文将|如上所述的条文|由系统|系统随后|随后的(风险清单|表|矩阵)|下表|如下表|见表|不对[^。]{0,20}作任何|不作任何(新增|调整)|以企业[^。]{0,12}判断为准")
# “该国未规定/没有规定……”：模型只看到几条条文，看不到的不等于没有
_NOT_PROVIDED = re.compile(r"(未|没有|并未|尚未)(作出|作|对此|就此|专门|明确|具体)?规定|未作要求|没有要求|未设定|没有设定|未具体")
_ZH_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _zh_number(s: str) -> int | None:
    s = s.strip().translate(_INDIC)
    if s.isdigit():
        return int(s)
    if not s or any(ch not in "零一二两三四五六七八九十百" for ch in s):
        return None
    total, cur = 0, 0
    for ch in s:
        if ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        else:
            cur = _ZH_DIGIT[ch]
    return total + cur


def _article_mismatch(text: str, cited: list[dict]) -> str:
    """The first “第X条” in the text that is neither the article of a clause it cites nor mentioned
    inside one (a cross-reference); "" when all are. A sentence naming another article than the one
    it rests on reads as a citation of the wrong clause."""
    if not cited:
        return ""
    own: set[int] = set()
    inside = ""
    for c in cited:
        label = _clause_label(str(c.get("clause") or ""))
        m = re.search(r"\d+|[零一二两三四五六七八九十百]+", label)
        if m and (n := _zh_number(m.group(0))) is not None:
            own.add(n)
        for x in re.findall(r"(?<![\d.])(?:\d+(?!\.\d)|[零一二两三四五六七八九十百]+)", label):  # 标签可以带几个条号（带小数点的段号不拆）
            if (n := _zh_number(x)) is not None:
                own.add(n)
        inside += str(c.get("text") or "") + str(c.get("original") or "")
    inside_nums = {_zh_number(x) for x in re.findall(r"第\s*([0-9٠-٩]+|[零一二两三四五六七八九十百]+)\s*条", inside)}
    inside_nums |= {int(x) for x in re.findall(r"(?:article|art\.|section|المادة|الفصل)\s*([0-9]+)", inside, re.IGNORECASE)}
    for m in re.finditer(r"第\s*([0-9٠-٩]+|[零一二两三四五六七八九十百]+)\s*条", text):
        n = _zh_number(m.group(1))
        asserts = re.match(r"\s*(第[^，。]{1,6}(款|项))?\s*(规定|要求|明确|设立|禁止|允许|列明|赋予|授权)", text[m.end():])
        if n is not None and n not in own and (asserts or n not in inside_nums):
            return m.group(0)  # 说“第X条规定……”的必须就是所引那一条；交叉引用的只能在摘录里出现过
    # 指南、标准的段落号（“第6.1.2段”“6.1.4”）：必须是所引那一段，或在所引摘录里出现
    dotted_own = {re.sub(r"[^0-9.]", "", _clause_label(str(c.get("clause") or ""))) for c in cited}
    for m in re.finditer(r"(?<![0-9.])(\d+(?:\.\d+){1,3})(?:\s*(段|条|项|节))?", text):
        num = m.group(1)
        if (m.group(2) or re.search(r"第\s*$", text[: m.start()])) and num not in dotted_own and num not in inside:
            return m.group(0)
    return ""


def _cite_line(cites: list[int], offered: dict[int, dict], refs: dict) -> str:
    """“[1][3]”: the number of each cited clause in the table at the end of the paper, sorted ascending."""
    nums: list[int] = []
    for n in cites:
        key = (offered[n]["document"], offered[n]["clause"])
        refs.setdefault(key, (len(refs) + 1, offered[n]["text"]))
        nums.append(refs[key][0])
    return "".join(f"[{idx}]" for idx in sorted(set(nums)))


async def write_section(
    section: Section,
    values: dict[str, str],
    facts_labels: dict[str, str],
    citations: list[dict],
    context: dict,
    refs: dict,
    *,
    sibling_headings: list[str] | None = None,
) -> tuple[list[dict], int, list[str]]:
    facts = {facts_labels.get(k, k): risk_items.shown(k, values.get(k, ""), MISSING_FACT) or MISSING_FACT for k in section.facts}
    context_blob = json.dumps(
        [facts, context.get("scenario", ""), context.get("vehicle", ""), context.get("project_facts", {})],
        ensure_ascii=False,
    )
    pool = _select_section_pool(citations, section.dimensions, section.side, context_blob)
    offered_list = _offered(citations, section.dimensions, section.side, context_blob)
    offered = {c["n"]: c for c in offered_list}
    offered_docs = [c["document"] for c in offered_list]
    role = _resolve_role(section)
    is_final = role in ("risk_and_verdict", "conclusive_ruling")
    doc_style = _resolve_doc_style(context, section)

    user_payload: dict[str, Any] = {
        "section": section.heading,
        "style_guide": _DOCTYPE_STYLE_GUIDES[doc_style],
        "role_guide": _EXEMPLAR_ROLE_GUIDES[role],
        "structural_exemplar": _pick_exemplar(role, doc_style, section, context),
        "allow_final_verdict": is_final,
        "must_argue": section.brief,
        "context": context,
        "facts": facts,
        "clauses": offered_list,
        "schema": _schema(section),
    }
    if sibling_headings:
        user_payload["sibling_chapters"] = sibling_headings
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        },
    ]
    allowed = _numbers(json.dumps(facts, ensure_ascii=False)) | _numbers(json.dumps(context, ensure_ascii=False))
    allowed |= offered_numbers(pool, offered_list)  # 法规名称里的编号（如 09-08）也算条文自带
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", json.dumps([facts, context, offered_list], ensure_ascii=False))}
    # 有段落没通过核对（引了没给的条文、夹了英文词等）就整节再写一次，并告诉模型上次的问题：
    # 只删段落会留下接不上的半截论述（如开头“该机制还受……”）。两次里取丢段少的一次。
    tokens = 0
    best: tuple[dict, list[dict], list[str]] | None = None
    retried_trunc = False
    rewrote = False
    for _ in range(3):
        try:
            res = await call_llm_json(
                messages,
                max_tokens=14000 if retried_trunc else 10000,
                temperature=0.0,
                timeout=240.0,
            )
        except LlmUnavailableError as exc:
            if best is not None:
                break
            if not retried_trunc and (
                "truncated" in str(exc)
                or "截断" in str(exc)
                or any(f.get("reason") == "output_truncated" for f in getattr(exc, "failures", []))
            ):
                retried_trunc = True
                messages = [
                    *messages[:2],
                    {
                        "role": "user",
                        "content": "上一次输出被长度上限截断。请精炼内部思考过程，将 paragraphs 控制在两段（每段不超过二百一十个字），完整输出合法 JSON。",
                    },
                ]
                continue
            raise NarrativeError(str(exc)) from exc
        data = res.data if isinstance(res.data, dict) else {}
        tokens += int(getattr(res, "prompt_tokens", 0) or 0) + int(getattr(res, "completion_tokens", 0) or 0)
        kept: list[dict] = []
        why_list: list[str] = []
        for item in data.get("paragraphs") or []:
            ok, why = _check_paragraph(item, offered, allowed, words)
            if ok is None:
                why_list.append(why)
            else:
                kept.append(ok)
        if best is None or (kept and (not best[1] or len(why_list) < len(best[2]))):
            best = (data, kept, why_list)
        if not why_list or rewrote:
            break
        rewrote = True
        messages = [*messages[:2], {"role": "user", "content": "上次输出有段落未通过核对：" + "；".join(why_list)
                                    + "。请重写整个部分：cites 只能用 clauses 里给出的 n；正文只用中文，不夹英文词；每段都要能独立读懂。"}]
    data, kept, why_list = best
    dropped = [f"{section.key}: {w}" for w in why_list]
    blocks: list[dict] = [{"type": "heading", "text": section.heading}]
    for ok in kept:
        prose = _normalize_statute_mentions(polish_legal_prose(ok["text"]), offered_docs)
        if not is_final:
            prose = _strip_premature_verdict(prose)
        blocks.append({"type": "paragraph", "text": prose + _cite_line(ok["cites"], offered, refs)})
    if len(blocks) == 1:
        raise NarrativeError(f"“{section.heading}”没有得到通过核对的论述（{'; '.join(dropped) or '模型无输出'}）")
    if section.flow:
        rows = []
        for step in data.get("flow") or []:
            if isinstance(step, dict) and step.get("stage"):
                rows.append(
                    [polish_legal_prose(str(step.get(k) or MISSING_FACT)[:240]) for k in ("stage", "data", "where_who", "safeguard")]
                )
        if rows:
            blocks.append({"type": "paragraph", "text": "数据处理流程图（按数据流动顺序，箭头表示流向）："})
            flow = [r[0] for r in rows]
            blocks.append({"type": "paragraph", "text": " → ".join(flow)})
            blocks.append(
                {"type": "table", "head": ["环节", "处理的数据", "地点与主体", "保护措施"], "rows": rows}
            )
    if section.field_table:
        field_rows = []
        for item in data.get("field_items") or []:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            cells = [polish_legal_prose(str(item.get(k) or MISSING_FACT).strip()[:240]) for k in ("name", "desc", "necessity", "example", "remark")]
            joined_cells = " ".join(cells)
            if _numbers(joined_cells) - allowed:
                continue
            if re.search(r"条文\s*\d|第\s*\d+\s*号条文|\bfacts?\b|\bclauses?\b|\bcites?\b", joined_cells, re.I):
                continue
            field_rows.append(cells)
        if field_rows:
            blocks.append(
                {
                    "type": "table",
                    "head": ["序号", "数据项名称", "内容描述", "出境必要性", "示例", "备注"],
                    "widths": [7, 16, 23, 25, 14, 15],
                    "rows": [[str(idx), *r] for idx, r in enumerate(field_rows, 1)],
                }
            )
    if section.risks:
        # 风险清单与矩阵按企业逐行填的风险生成（等级由矩阵算出），不让模型替企业定等级
        key = next((k for k in section.facts if k in risk_items.STRUCTURED), "")
        if key and risk_items.parse(values.get(key, "")):
            blocks.extend(risk_items.blocks(values[key], MISSING_FACT))
    return blocks, tokens, dropped


def _merge_section_refs(blocks: list[dict], local_refs: dict, global_refs: dict) -> list[dict]:
    if not local_refs:
        return blocks
    idx_map: dict[int, int] = {}
    for key, (local_idx, txt) in sorted(local_refs.items(), key=lambda kv: kv[1][0]):
        global_refs.setdefault(key, (len(global_refs) + 1, txt))
        idx_map[local_idx] = global_refs[key][0]
    cite_re = re.compile(r"\[(\d+)\]")
    cluster_re = re.compile(r"(?:\[\d+\])+")

    def _rw_cluster(m: re.Match) -> str:
        nums = [idx_map.get(int(s.group(1)), int(s.group(1))) for s in cite_re.finditer(m.group(0))]
        return "".join(f"[{x}]" for x in sorted(dict.fromkeys(nums)))

    out: list[dict] = []
    for b in blocks:
        nb = dict(b)
        if isinstance(nb.get("text"), str):
            nb["text"] = cluster_re.sub(_rw_cluster, nb["text"])
        out.append(nb)
    return out


def _extract_canonical_field_names(blocks: list[dict]) -> list[str]:
    names: list[str] = []
    for b in blocks:
        if b.get("type") == "table" and (b.get("head") or [])[:2] == ["序号", "数据项名称"]:
            for r in b.get("rows") or []:
                if len(r) >= 2 and str(r[1] or "").strip() and str(r[1]).strip() != MISSING_FACT:
                    nm = str(r[1]).strip()
                    if nm not in names:
                        names.append(nm)
    return names


async def write_paper(
    definition: dict, values: dict[str, str], citations: list[dict], context: dict
) -> Paper:
    labels = {f["key"]: f["label"] for f in definition.get("facts", [])}
    paper = Paper()
    refs: dict[tuple[str, str], tuple[int, str]] = {}
    sections = narrative_sections(definition)
    sem = asyncio.Semaphore(4)
    all_headings = [s.heading for s in sections]
    ordered_results: dict[int, tuple[list[dict], int, list[str], dict]] = {}
    shared_ctx = dict(context)

    async def _one(idx: int, sec: Section, ctx_now: dict) -> None:
        lrefs: dict[tuple[str, str], tuple[int, str]] = {}
        siblings = [h for j, h in enumerate(all_headings) if j != idx]
        async with sem:
            blks, tks, drp = await write_section(
                sec, values, labels, citations, ctx_now, lrefs, sibling_headings=siblings
            )
        ordered_results[idx] = (blks, tks, drp, lrefs)

    final_roles = ("risk_and_verdict", "conclusive_ruling")
    stage1 = [(i, s) for i, s in enumerate(sections) if s.field_table]
    stage3 = [(i, s) for i, s in enumerate(sections) if not s.field_table and _resolve_role(s) in final_roles]
    stage2 = [
        (i, s)
        for i, s in enumerate(sections)
        if not s.field_table and _resolve_role(s) not in final_roles
    ]

    if stage1:
        await asyncio.gather(*(_one(i, s, shared_ctx) for i, s in stage1))
        canon_names: list[str] = []
        for i, _ in stage1:
            for nm in _extract_canonical_field_names(ordered_results[i][0]):
                if nm not in canon_names:
                    canon_names.append(nm)
        stage2_ctx = dict(shared_ctx)
        if canon_names:
            stage2_ctx["标准数据项名称"] = canon_names
        stage2_ctx["前序章节去重约束"] = (
            "系统性描述章节已完成全量数据项拆解与采集至销毁流水线刻画；本章严禁再次用顿号逐一背诵全部数据项名称，只聚焦本章专属法理命题。"
        )
    else:
        stage2_ctx = dict(shared_ctx)

    if stage2:
        await asyncio.gather(*(_one(i, s, stage2_ctx) for i, s in stage2))

    if stage3:
        stage3_ctx = {
            **stage2_ctx,
            "前序章节去重约束": (
                "前序各章已完成主体治理、数据项清单、合法性、替代比选及具体风险压降分析；结论章只作终局法理收束、放行硬门禁与动态复评条件裁定，严禁重复展开具体威胁链条或罗列传感器清单。"
            ),
        }
        await asyncio.gather(*(_one(i, s, stage3_ctx) for i, s in stage3))

    for i in range(len(sections)):
        blocks, tokens, dropped, lrefs = ordered_results[i]
        paper.blocks.extend(_merge_section_refs(blocks, lrefs, refs))
        paper.tokens += tokens
        paper.dropped.extend(dropped)
    if refs:
        orig_of: dict[str, str] = {}
        for c in citations:
            dt = str(c.get("document_title") or "").strip()
            ot = str(c.get("original_title") or "").strip()
            if dt and ot and ot != dt and dt not in orig_of:
                orig_of[dt] = ot
        seen_docs: set[str] = set()
        ref_rows: list[list[str]] = []
        for (doc, clause), (n, text) in refs.items():
            orig = orig_of.get(doc, "")
            if orig and doc not in seen_docs and orig not in doc:
                seen_docs.add(doc)
                law_disp = f"{doc}（{orig}）"
            else:
                seen_docs.add(doc)
                law_disp = doc
            ref_rows.append([f"[{n}]", f"{law_disp} {_clause_label(clause)}".strip(), clause_gate.cut(text)])
        paper.blocks.append({"type": "heading", "text": "参考条文"})
        paper.blocks.append(
            {
                "type": "table",
                "head": ["编号", "法规与条款", "条文摘录"],
                "widths": [8, 32, 60],
                "rows": ref_rows,
            }
        )
        paper.cited = cited_ids(set(refs), citations)
    return paper


def cited_ids(keys: set[tuple[str, str]], citations: list[dict]) -> list[str]:
    """The clause ids behind the (law, article) pairs a paper cites: the clauses it was offered
    are these citations, keyed the same way (``_offered``)."""
    out: list[str] = []
    for c in citations:
        key = (str(c.get("document_title", "")), str(c.get("clause_number", "")))
        cid = str(c.get("clause_id") or "")
        if key in keys and cid and cid not in out:
            out.append(cid)
    return out


def cited_in_blocks(blocks: list[dict], citations: list[dict]) -> list[str]:
    """For papers stored before their cited ids were kept: the rows of the paper's own table of
    cited clauses, read back exactly as ``write_paper`` wrote them, against the same citations."""
    rows: set[str] = set()
    for b in blocks or []:
        if b.get("type") == "table" and b.get("head") == ["编号", "法规与条款", "条文摘录"]:
            for r in b.get("rows", []):
                if len(r) > 1 and isinstance(r[1], str):
                    rows.add(r[1].strip())
                    rows.add(re.sub(r"（[^）]+）", "", r[1]).strip())
    keys = {
        (str(c.get("document_title", "")), str(c.get("clause_number", "")))
        for c in citations
        if f"{c.get('document_title', '')} {_clause_label(str(c.get('clause_number', '')))}".strip() in rows
    }
    return cited_ids(keys, citations)


def facts_hash(definition: dict, values: dict[str, str]) -> str:
    """The facts a paper is written from, without the clauses: the same facts with a changed
    library is the same paper with outdated citations, not a paper about something else."""
    payload = {
        "narrative": definition.get("narrative", []),
        "title": definition.get("title"),
        "values": {k: values.get(k, "") for k in sorted(values)},
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def usable(
    session: AsyncSession, task, code: str, definition: dict, values: dict[str, str], citations: list[dict]
) -> tuple[DocumentNarrative | None, bool]:
    """(paper, basis_updated). The paper written from exactly the current facts and clauses; else
    the latest one written from the same facts (the library has moved on since: its citations may
    be outdated, so ``basis_updated``); else none. Papers stored before the facts were recorded with
    them count as the same facts when nothing about the project changed after they were written."""
    exact = await stored(session, task.id, code, inputs_hash(definition, values, citations))
    if exact is not None:
        return exact, False
    want = facts_hash(definition, values)
    rows = (
        await session.scalars(
            select(DocumentNarrative)
            .where(DocumentNarrative.task_id == task.id, DocumentNarrative.code == code)
            .order_by(DocumentNarrative.created_at.desc())
        )
    ).all()
    for row in rows:
        mark = (row.report or {}).get("facts_hash")
        if mark == want or (mark is None and task.updated_at and row.created_at and row.created_at >= task.updated_at):
            # 引用的条文有更新：只在被引用的那条条文的原文真的变了才算（法规首次入库、平台自己重新生成文件都不算）
            return row, bool(await cited_changed(session, row))
    return None, False


async def cited_changed(session: AsyncSession, paper: DocumentNarrative) -> list[str]:
    """The clauses a paper cites whose wording changed since: the law has a newer parsed version
    whose clause of the same number reads differently (or is gone). Labelled “《法规名》第 N 条”."""
    from app.admission import names
    from app.ingestion.models import Clause, Document, DocumentVersion

    cids: list[uuid.UUID] = []
    for raw in (paper.report or {}).get("cited_clause_ids") or []:
        try:
            cid = uuid.UUID(str(raw))
        except ValueError:
            continue
        if cid not in cids:
            cids.append(cid)
    if not cids:
        return []

    rows_by_id = {
        r[0].id: r
        for r in (
            await session.execute(
                select(Clause, DocumentVersion, Document)
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Clause.id.in_(cids))
            )
        ).all()
    }
    if not rows_by_id:
        return []

    doc_ids = list({r[2].id for r in rows_by_id.values()})
    newest_by_doc: dict[uuid.UUID, DocumentVersion] = {}
    for dv in (
        await session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id.in_(doc_ids), DocumentVersion.state == "parsed")
            .order_by(DocumentVersion.document_id, DocumentVersion.version_no.desc())
        )
    ).all():
        newest_by_doc.setdefault(dv.document_id, dv)

    newer_ver_ids = list({
        newest_by_doc[doc.id].id
        for clause, version, doc in rows_by_id.values()
        if doc.id in newest_by_doc
        and newest_by_doc[doc.id].id != version.id
        and newest_by_doc[doc.id].version_no > version.version_no
    })
    if not newer_ver_ids:
        return []

    newer_hashes: dict[tuple[uuid.UUID, str | None], str | None] = {}
    for vid, cnum, thash in (
        await session.execute(
            select(Clause.version_id, Clause.clause_number, Clause.text_hash).where(Clause.version_id.in_(newer_ver_ids))
        )
    ).all():
        newer_hashes.setdefault((vid, cnum), thash)

    out: list[str] = []
    for cid in cids:
        row = rows_by_id.get(cid)
        if row is None:
            continue
        clause, version, doc = row
        newest = newest_by_doc.get(doc.id)
        if newest is None or newest.id == version.id or newest.version_no <= version.version_no:
            continue
        same = newer_hashes.get((newest.id, clause.clause_number))
        if same is not None and same == clause.text_hash:
            continue
        num = (clause.clause_number or "").strip("()（） ")
        label = f"《{names.law_name(str(doc.id), doc.title_zh or doc.title)}》" + (f"第 {num} 条" if num else "")
        if label not in out:
            out.append(label)
    return out[:4]


async def stored(session: AsyncSession, task_id: uuid.UUID, code: str, digest: str) -> DocumentNarrative | None:
    return await session.scalar(
        select(DocumentNarrative).where(
            DocumentNarrative.task_id == task_id,
            DocumentNarrative.code == code,
            DocumentNarrative.inputs_hash == digest,
        )
    )


_BARE_CORE_LAW_RE = re.compile(
    r"(?:依|依据|根据|按照|对照)\s*(?:个人数据保护基本法|个人数据保护法|个人信息保护法|数据安全法|网络安全法|通用数据保护条例|电子交易与个人数据法)(?:（[^）]+）)?\s*第\s*[0-9一二三四五六七八九十百零]+\s*条"
)


def audit_document_prose_quality(
    blocks: list[dict], doc_code: str = "", context_text: str = ""
) -> list[str]:
    """Editorial quality gate catching mechanical writing defects before any compliance document ships."""
    issues: list[str] = []
    chapter_paras: dict[str, list[str]] = {}
    cur_heading = "前言"
    narrative_paras: list[str] = []

    for b in blocks or []:
        btype = b.get("type")
        if btype == "heading":
            cur_heading = str(b.get("text") or "").strip()
            continue
        if btype == "table":
            head = [str(x or "") for x in (b.get("head") or [])]
            rows = b.get("rows") or []
            for r in rows:
                for cell in r:
                    cs = str(cell or "")
                    if re.search(r"【[^】]{4,}[。！？]】", cs):
                        issues.append(f"{cur_heading}: 表格单元格存在方括号吞入完整句号（{cs[:36]}）")
                    if re.search(r"结合企业申报依据：[^。\n]{0,36}：", cs) or "：：" in cs:
                        issues.append(f"{cur_heading}: 表格单元格存在双冒号机械拼接（{cs[:36]}）")
            if "措施适当性与有效性论证" in head and len(rows) >= 2:
                col_idx = head.index("措施适当性与有效性论证")
                vals = [str(r[col_idx] or "").strip() for r in rows if len(r) > col_idx]
                if len(vals) >= 2 and len(set(vals)) == 1:
                    issues.append(f"{cur_heading}: 措施适当性与有效性列多行完全同质化盖章")
                if any(v.startswith("依托既有控制（") for v in vals):
                    issues.append(f"{cur_heading}: 措施适当性与有效性列残留旧式‘依托既有控制（…）’套话")
            continue

        if btype == "paragraph":
            txt = str(b.get("text") or "").strip()
            if not txt:
                continue
            if re.search(r"【[^】]{4,}[。！？]】", txt):
                issues.append(f"{cur_heading}: 段落存在方括号吞入完整句号（{txt[:36]}）")
            if re.search(r"（[^（）]{10,}[。！？]）[，。；]", txt):
                issues.append(f"{cur_heading}: 段落存在圆括号内嵌完整句号（{txt[:36]}）")
            if cur_heading == "参考条文" or txt.endswith("：") or " → " in txt:
                continue
            chapter_paras.setdefault(cur_heading, []).append(txt)
            # 带引用标号 [n] 的论述段计入模型正文统计
            if re.search(r"\[\d+\]$", txt):
                narrative_paras.append(txt)
                if _BARE_CORE_LAW_RE.search(txt):
                    issues.append(f"{cur_heading}: 引用专门法律缺失中文书名号《》（{txt[:48]}）")
                cited_titles = re.findall(r"《([^》]{5,})》\s*第\s*[0-9一二三四五六七八九十百零]+", txt)
                for title in set(cited_titles):
                    if cited_titles.count(title) >= 3:
                        issues.append(f"{cur_heading}: 单段内机械重复《{title}》全称 {cited_titles.count(title)} 次")
                if not re.search(r"科学研究|学术研究|统计调查|临床研究", context_text) and re.search(
                    r"科学研究场景|科研目的再处理|超出科研数据|符合科学研究", txt
                ):
                    issues.append(f"{cur_heading}: 商业车载业务误套学术‘科学研究’豁免条款")

    # 跨章枚举复读检查：同一串含两个以上顿号、长度>=14字的枚举短语不得在3个及以上章节原样复读
    enum_chapters: dict[str, set[str]] = {}
    for ch, paras in chapter_paras.items():
        ch_blob = "\n".join(paras)
        for m in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9（）()\-]{2,18}(?:、[\u4e00-\u9fffA-Za-z0-9（）()\-]{2,22}){2,}", ch_blob):
            phrase = m.group(0)[:36]
            if len(phrase) >= 14:
                enum_chapters.setdefault(phrase, set()).add(ch)
    for phrase, chs in enum_chapters.items():
        if len(chs) >= 3:
            issues.append(f"跨章报菜名复读（出现在 {len(chs)} 章）：{phrase}…")

    # 全篇高频机械口头禅检查
    all_nar = "\n".join(narrative_paras)
    xianjie_cnt = len(re.findall(r"衔接", all_nar))
    if xianjie_cnt >= 6:
        issues.append(f"全篇论述高频堆砌‘衔接’套话（共 {xianjie_cnt} 次）")

    return issues


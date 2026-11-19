"""咨询层:在已核验的证据账本之上,按业务事实写出结论、差距、风险与建议。

证据层(检索、选证、子串与数字核验)保持不变;本模块只读取其结果,不增加法律依据。
模型负责"怎么分析、怎么建议",程序负责"能不能这么写":

- 法律性陈述只能通过 evidence_id 指向已核验证据,角标编号由程序分配;
- 业务现状只能来自用户提供的业务事实(fact_key),缺什么就写缺什么;
- 数字只能来自证据或用户事实;文字不得大段照抄证据原文或译文;
- 风险等级由程序按"可能性 x 影响"计算,模型只给两个因子和理由;
- 每个中高风险差距都必须有建议,建议必须落到具体差距。

生成失败不影响证据版报告交付,调用方保留原报告并记录原因。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.analysis.llm import LlmResult, LlmUnavailableError, call_llm_json
from app.analysis.rich_text import bold_problems, limit_bold, strip_bold

SCHEMA_VERSION = "consulting-v2"
PROMPT_VERSION = "consulting-prompt-v2"

# 八个方面由甲方规定（编码固定、与国家无关）；这里只决定每个方面的写法：
# 解释型（先讲清框架/定义，再说对客户的影响）、评估型（逐项对照现状）、
# 落地型（把全部建议汇成分阶段的整改路线图，第八个方面本身就是路线图）。
ROADMAP_DIMENSION = "D8"
EXPLAIN_DIMENSIONS = frozenset({"D1", "D2"})
PHASE_LABELS = {1: "第一阶段：30 天内", 2: "第二阶段：31–60 天", 3: "第三阶段：61 天以后"}
# 八个方面的简称：用在文件名和附录小标题里（正式标题太长，且同一方面在不同国家的报告里标题可能略有差异）
TOPIC_NAMES = {
    "D1": "法规体系",
    "D2": "数据定义与分类",
    "D3": "数据跨境传输",
    "D4": "授权告知同意",
    "D5": "存储留存销毁",
    "D6": "违规处罚追责",
    "D7": "市场准入",
    "D8": "整改建议与落地方案",
}


def phase_of(days: int) -> int:
    return 1 if days <= 30 else 2 if days <= 60 else 3


AssessmentStatus = Literal["compliant", "partial", "non_compliant", "insufficient_info"]
ActionCategory = Literal["vehicle", "data_architecture", "cloud", "organization"]
CATEGORY_LABEL = {
    "vehicle": "车端",
    "data_architecture": "数据架构",
    "cloud": "云端",
    "organization": "组织与流程",
}
STATUS_LABEL = {
    "compliant": "已达到",
    "partial": "部分达到",
    "non_compliant": "未达到",
    "insufficient_info": "需补充信息",
}
LEVEL_LABEL = {"high": "高", "medium": "中", "low": "低", "unknown": "待评估"}
VERDICTS = ("可行", "有条件可行", "暂不建议", "信息不足")

MAX_ASSESSMENTS = 5  # 每个方面只写最要紧的几条，读者读得完
MAX_ATTEMPTS = 2
COPY_WINDOW = 24  # 连续照抄证据原文/译文达到此长度即拒绝

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_BRACKET_REF = re.compile(r"\[\s*\d+\s*\]")
# 内部编号和系统词不能出现在给业务人员看的文字里（维度编号 D1-D8、证据编号 F-01、建议编号 A-01）
_INTERNAL_ID = re.compile(r"(?<![A-Za-z0-9])(?:D[1-8]|F-\d+|A-\d+)(?![A-Za-z0-9])")
_SYSTEM_WORDS = ("evidence_id", "evidence", "原子规则", "维度编号")


# ---------------------------------------------------------------- 模型输出 Schema


class AssessmentItem(BaseModel):
    requirement: str = Field(min_length=4, max_length=300)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)
    business_fact_keys: list[str] = Field(default_factory=list, max_length=6)
    business_state: str = Field(default="", max_length=400)
    status: AssessmentStatus
    likelihood: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    # 信息不足时模型常漏写理由；缺省时由 needs_fact 补，其余情形仍要求写明
    rationale: str = Field(default="", max_length=600)
    needs_fact: str = Field(default="", max_length=300)

    @field_validator("status", mode="before")
    @classmethod
    def _status_synonyms(cls, v):
        # 模型有时写中文或近义英文；只映射明确同义的写法，其他值仍报错，不猜
        if not isinstance(v, str):
            return v
        key = re.sub(r"[\s\-_]+", "", v.strip().lower())
        table = {
            "compliant": "compliant",
            "已达到": "compliant",
            "符合": "compliant",
            "partial": "partial",
            "partiallycompliant": "partial",
            "部分达到": "partial",
            "部分符合": "partial",
            "noncompliant": "non_compliant",
            "notcompliant": "non_compliant",
            "未达到": "non_compliant",
            "不符合": "non_compliant",
            "insufficientinfo": "insufficient_info",
            "insufficientinformation": "insufficient_info",
            "信息不足": "insufficient_info",
        }
        return table.get(key, v)

    @field_validator("needs_fact", mode="before")
    @classmethod
    def _no_ask_prefix(cls, v):
        # 展示时已有“需要补充”的标签，模型自带的“请补充：”会读成两遍
        return re.sub(r"^\s*(请|需要)?补充[：:]?\s*", "", v) if isinstance(v, str) else v

    @field_validator("likelihood", "impact", mode="before")
    @classmethod
    def _zero_means_unscored(cls, v):
        # 已达到的事项模型常写 0 表示“没有风险”：当作未打分，不因格式整份打回
        # （存在差距却没打分，仍由 validate_dimension 要求补上）
        return None if v in (0, "0", "") else v

    @model_validator(mode="after")
    def _rationale_required_unless_asking(self):
        if not self.rationale.strip():
            if self.status == "insufficient_info" and self.needs_fact.strip():
                self.rationale = "目前缺少这方面的业务事实，暂时下不了结论"
            else:
                raise ValueError("缺少判断理由 rationale")
        return self

    @model_validator(mode="after")
    def _insufficient_info_is_not_scored(self):
        # 信息不足本来就不打分：模型多给的两个因子直接忽略，不因此整份打回
        if self.status == "insufficient_info":
            self.likelihood = self.impact = None
        return self


def short_title(text: str, limit: int = 22) -> str:
    """没有写短标题时，取动作的第一个分句作为短标题（只截取，不改写）。"""
    first = re.split(r"[，。；：,;:]", strip_bold(text).strip(), maxsplit=1)[0].strip()
    return first if len(first) <= limit else first[:limit] + "…"


def build_conclusive_matrix(consulting: dict) -> dict:
    """Reference DPIA Chapter 4 & Chapter 6: deterministic quantitative risk closure & four-tier conclusive decision table."""
    dims = consulting.get("dimensions") or []
    counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    has_assessments = False
    all_req_text = []
    for d in dims:
        for a in d.get("assessments") or []:
            has_assessments = True
            lv = a.get("level") or "unknown"
            if lv in counts:
                counts[lv] += 1
            all_req_text.append(strip_bold(str(a.get("requirement") or "")))
    if not has_assessments and isinstance(consulting.get("risk_counts"), dict):
        for k in counts:
            counts[k] = int(consulting["risk_counts"].get(k, 0) or 0)

    verdict = consulting.get("verdict") or {}
    v_label = str(verdict.get("label") or "信息不足")
    roadmap = consulting.get("roadmap") or []
    must_ids = set(verdict.get("must_do_action_ids") or [])
    must_acts = [a for a in roadmap if a.get("id") in must_ids] or roadmap[:3]
    joined_corpus = " ".join(all_req_text + [strip_bold(str(x.get("title") or "") + " " + str(x.get("action") or "")) for x in roadmap])
    needs_filing = bool(re.search(r"安全评估|标准合同|备案|事先授权|许可|批准|申报|协议|合同", joined_corpus))

    is_reject = v_label in ("暂不建议", "信息不足")
    is_cond = v_label == "有条件可行"
    is_pass = v_label == "可行"

    must_summary = "；".join(
        f"（{idx}）{strip_bold(str(x.get('title') or short_title(str(x.get('action') or ''))))}（{x.get('owner_role', '责任部门')}，{x.get('due_in_days', 30)} 天内）"
        for idx, x in enumerate(must_acts, 1)
    )

    tiers = [
        {
            "option": "驳回或暂缓实施（终止或重新设计）",
            "mark": "■ 暂缓实施" if is_reject else "□ 不适用",
            "basis": (
                f"当前识别高风险差距 {counts['high']} 项、待补充关键事实 {counts['unknown']} 项；在消除阻断性违法缺口或补齐核心业务事实前，不得按原方案直接启动业务。"
                if is_reject
                else "经逐项比对现行法规要件，未发现触发法定绝对禁止红线或穷尽技术合同措施后仍无法压降的不可控风险，无需整体驳回业务方案。"
            ),
        },
        {
            "option": "依法履行事前申报审批、备案或许可",
            "mark": "■ 适用必办" if needs_filing else "□ 按需核验",
            "basis": (
                "须在跨境传输或属地业务上线前完结适用的法定前置程序（如跨境传输协议签署备案、安全评估申报或目标国主管机关事先许可），留存完整法务核验凭证。"
                if needs_filing
                else "当前已核验条款未额外触发专项行政审批前置门槛，后续如传输规模或敏感数据范围扩张须及时复核。"
            ),
        },
        {
            "option": "有条件通过（落实刚性前置条件后实施）",
            "mark": "■ 正式核定" if is_cond else ("□ 待阻断项闭环后转入" if is_reject else "□ 已满足基础条件"),
            "basis": (
                (f"须优先闭环刚性前置事项：{must_summary}。" if must_summary else "须严格落实各维度技术脱敏、加密访问控制与合同约束措施。")
                + "同时执行单路径暂停与本地降级隔离机制——任一数据子流或传输路径未完结前置手续或监测到异常高风险时，立即单独暂停该路径并切换本地处理预案，其余已合规路径隔离运行。"
            ),
        },
        {
            "option": "无条件通过",
            "mark": "■ 合规可行（维持内控复审）" if (is_pass and counts["high"] == 0 and counts["medium"] == 0) else "□ 不适用",
            "basis": (
                "当前各项核验要求均已达到且无中高风险差距，可在维持现有车端最小化控制、到期自动销毁与定期审计复评的前提下开展业务。"
                if (is_pass and counts["high"] == 0 and counts["medium"] == 0)
                else "汽车数据跨境流转受双边监管动态、采集字段边界与留存期限持续约束，须绑定前置整改条件与周期复审机制，不适用无附加条件放行。"
            ),
        },
    ]
    closure_rule = (
        f"量化风险收口核定：按发生可能性（1–5）×影响严重度（1–5）定量评估，当前共识别高风险 {counts['high']} 项、中风险 {counts['medium']} 项、"
        f"低风险（含已达标）{counts['low']} 项"
        + (f"、待补充确认 {counts['unknown']} 项。" if counts["unknown"] else "。")
        + "遵循“消除＞降低＞转移＞接受”风险处置优先级，所有高风险差距须通过源端最小化脱敏、加密隔离与合同约束压降至中、低可接受区间后方可正式放行。"
    )
    return {"counts": counts, "tiers": tiers, "closure_rule": closure_rule}


def upgrade_legacy(consulting: dict | None) -> dict | None:
    """Fill the fields older stored reports lack so every reader can rely on the current shape.

    Early reports kept the roadmap without a short title, a phase or dependencies. All three can
    be derived from what is stored (the action text and the deadline); nothing is invented, and
    the stored report itself is not rewritten.
    """
    if not consulting:
        return consulting
    out = dict(consulting)
    if consulting.get("roadmap"):
        roadmap = []
        for item in consulting["roadmap"]:
            x = dict(item)
            x.setdefault("title", short_title(str(x.get("action") or "")) or "整改事项")
            if not x.get("title"):
                x["title"] = short_title(str(x.get("action") or "")) or "整改事项"
            if "phase" not in x and isinstance(x.get("due_in_days"), int):
                x["phase"] = phase_of(x["due_in_days"])
            x.setdefault("depends_on", [])
            roadmap.append(x)
        out["roadmap"] = roadmap
    if "verdict" in out and not out.get("conclusive_matrix"):
        out["conclusive_matrix"] = build_conclusive_matrix(out)
    return out


class ActionItem(BaseModel):
    category: ActionCategory
    title: str = Field(default="", max_length=40)
    action: str = Field(min_length=4, max_length=300)
    owner_role: str = Field(min_length=2, max_length=60)
    due_in_days: int = Field(ge=1, le=365)
    done_criteria: str = Field(min_length=4, max_length=300)
    addresses: list[int] = Field(min_length=1, max_length=8)


class DimensionResponse(BaseModel):
    conclusion: str = Field(min_length=10, max_length=900)
    # 解释型方面：用一两段话向非专业读者讲清这个国家的框架/定义；其他方面留空
    overview: str = Field(default="", max_length=700)
    assessments: list[AssessmentItem] = Field(default_factory=list, max_length=MAX_ASSESSMENTS)
    actions: list[ActionItem] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _tidy(self):
        # 只清理版式前缀，不改动内容
        self.conclusion = re.sub(
            r"^\s*(\*\*)?(结论|总结|总体判断)\s*[:：](\*\*)?\s*", "", self.conclusion
        )
        for act in self.actions:
            # 信息不足的差距不需要整改：模型把它挂进建议时只去掉这条关联，建议本身保留
            act.addresses = [
                n
                for n in act.addresses
                if not (1 <= n <= len(self.assessments))
                or self.assessments[n - 1].status != "insufficient_info"
            ]
        self.actions = [a for a in self.actions if a.addresses]
        # 加粗只是强调：占比过高时由程序收敛（不改文字），不为此整段打回
        self.conclusion = limit_bold(self.conclusion)
        self.overview = limit_bold(self.overview.strip())
        for act in self.actions:
            act.title = limit_bold(act.title.strip()) or short_title(act.action)
        for a in self.assessments:
            a.business_state = limit_bold(a.business_state)
            a.rationale = limit_bold(a.rationale)
            a.requirement = limit_bold(a.requirement)
        for act in self.actions:
            act.action = limit_bold(act.action)
            act.done_criteria = limit_bold(act.done_criteria)
        return self


class ActionDependency(BaseModel):
    action_id: str
    after_action_id: str  # 必须先完成的建议


class SynthesisResponse(BaseModel):
    verdict: str
    # 先后依赖：action_id 必须在 after_action_id 完成之后才能做（例如先分类数据，才能决定哪些能出境）
    dependencies: list[ActionDependency] = Field(default_factory=list, max_length=12)
    headline: str = Field(min_length=6, max_length=120)
    summary: str = Field(min_length=20, max_length=900)
    must_do_action_ids: list[str] = Field(min_length=1, max_length=3)
    # 不同维度各自提出的实质相同的建议：每组是一批建议编号，保留第一个、其余并入
    duplicate_action_groups: list[list[str]] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _limit_bold(self):
        self.headline = limit_bold(self.headline)
        self.summary = limit_bold(self.summary)
        return self


# ---------------------------------------------------------------- 纯函数:风险与校验


def risk_level(likelihood: int | None, impact: int | None, status: str) -> tuple[int | None, str]:
    """风险等级由程序计算;不接受模型直接给等级。"""
    if status == "insufficient_info":
        return None, "unknown"
    if status == "compliant":
        return None, "low"
    if likelihood is None or impact is None:
        return None, "unknown"
    score = likelihood * impact
    return score, "high" if score >= 15 else "medium" if score >= 8 else "low"


def numbers_in(text: str) -> set[str]:
    return set(_NUMBER.findall(text or ""))


_SCALE_UNITS = {
    "thousand": 10**3,
    "million": 10**6,
    "billion": 10**9,
    "千": 10**3,
    "万": 10**4,
    "百万": 10**6,
    "千万": 10**7,
    "亿": 10**8,
    "十亿": 10**9,
}
_SCALED = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(thousand|million|billion|百万|千万|十亿|千|万|亿)", re.IGNORECASE
)


def scaled_values(text: str) -> set[int]:
    """文字里“数字 + 单位”的实际数值（如 $1 million、100 万 都是 1,000,000）。

    只用来识别“同一个数的不同写法”：中英文单位不同而数值相同时，不算凭空出现的数字。"""
    values: set[int] = set()
    for num, unit in _SCALED.findall(text or ""):
        try:
            values.add(round(float(num.replace(",", "")) * _SCALE_UNITS[unit.lower()]))
        except (ValueError, KeyError):
            continue
    return values


def unsupported_numbers(text: str, corpus: str) -> list[str]:
    """文字里不在依据/业务事实中的数字。

    - 原样出现在依据里的，算有来源；
    - 带单位写法的数值（$1 million = 100 万）与依据里的某个带单位数值相等的，也算有来源，
      以免把正确的中英文换算当成凭空数字；
    - 其余一律算没有来源。单位换算本身不会放宽“数字必须有来源”：500 万不等于依据里的任何数。"""
    allowed = scaled_values(corpus)
    bad: list[str] = []
    for num, unit in _SCALED.findall(text or ""):
        value = round(float(num.replace(",", "")) * _SCALE_UNITS[unit.lower()])
        if value not in allowed and num not in corpus:
            bad.append(num)
    covered = {n for n, _ in _SCALED.findall(text or "")}
    for n in numbers_in(text):
        if n in covered:
            continue
        if n not in corpus:
            bad.append(n)
    return sorted(set(bad))


def copies_source(text: str, sources: list[str], window: int = COPY_WINDOW) -> bool:
    """文字中是否连续照抄了证据原文/译文。只看足够长的连续片段。"""
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < window:
        return False
    grams = {compact[i : i + window] for i in range(len(compact) - window + 1)}
    for src in sources:
        s = re.sub(r"\s+", "", src or "")
        if len(s) >= window and any(s[i : i + window] in grams for i in range(len(s) - window + 1)):
            return True
    return False


def _absorb_into(dst: dict, src: dict) -> None:
    """把 src 并入 dst：保留较详细的动作（短标题随动作走）、责任角色、完成标准，取更紧的时限。"""
    if len(src["action"]) > len(dst["action"]):
        dst["action"] = src["action"]
        dst["title"] = src.get("title") or dst.get("title") or short_title(src["action"])
    for field in ("done_criteria", "owner_role"):
        if len(src[field]) > len(dst[field]):
            dst[field] = src[field]
    dst["due_in_days"] = min(dst["due_in_days"], src["due_in_days"])


def merge_same_title(actions: list[dict]) -> list[dict]:
    """短标题完全相同的建议就是同一件事（例如两个方面都提“完成保护水平评估”），合并成一条。

    不要求类别和阶段一致：真实输出里同一件事常被不同方面归入不同类别、写成不同时限，
    结果路线图里同一件事出现两次。关联差距取并集，时限取更紧的，不丢任何差距。"""
    merged: list[dict] = []
    for act in actions:
        key = re.sub(r"\s+", "", strip_bold(act.get("title") or ""))
        for kept in merged:
            if key and key == re.sub(r"\s+", "", strip_bold(kept.get("title") or "")):
                _absorb_into(kept, act)
                kept["addresses"] = list(dict.fromkeys([*kept["addresses"], *act["addresses"]]))
                break
        else:
            merged.append({**act, "addresses": list(act["addresses"])})
    return merged


def merge_similar_actions(actions: list[dict]) -> list[dict]:
    """同一类别、且解决的是同一批差距的建议，实质上是一件事，合并成一条。

    不用文字相似度判断：实测同一件事的两种写法字面相似度只有 0.2 左右，
    和“不同的两件事”几乎分不开；而“类别相同 + 关联差距集合相同”是结构信号，不会误合并。
    合并时保留较详细的动作、责任角色和完成标准，时限取更紧的；不丢任何差距。"""
    merged: list[dict] = []
    for act in actions:
        key = (act["category"], frozenset(act["addresses"]))
        for kept in merged:
            if (kept["category"], frozenset(kept["addresses"])) == key:
                _absorb_into(kept, act)
                break
        else:
            merged.append({**act, "addresses": list(act["addresses"])})
    return merged


def merge_overlapping_actions(actions: list[dict]) -> list[dict]:
    """同类别、同阶段、解决的差距有重叠的建议，也合并成一条。

    实测真实输出里同一件事常被几个方面分别提出（例如“保留期限”在数据架构和云端各提两三条），
    它们解决的差距并不完全相同，只是有交集，所以上一个“集合完全相同”的合并抓不到。
    这里按“类别 + 阶段 + 差距有交集”合并：同一个人在同一阶段里本来就是一起做这件事。
    关联差距取并集，所以每个差距仍然有建议覆盖；动作文字保留较详细的一条，时限取更紧的。
    不同类别（车端 / 数据架构 / 云端 / 组织）、不同阶段的建议不动。"""
    merged: list[dict] = []
    for act in actions:
        phase = phase_of(act["due_in_days"])
        for kept in merged:
            if (
                kept["category"] == act["category"]
                and phase_of(kept["due_in_days"]) == phase
                and set(kept["addresses"]) & set(act["addresses"])
            ):
                _absorb_into(kept, act)
                kept["addresses"] = list(dict.fromkeys([*kept["addresses"], *act["addresses"]]))
                break
        else:
            merged.append({**act, "addresses": list(act["addresses"])})
    return merged


def link_dimension_actions(results: list[dict], roadmap: list[dict]) -> None:
    """让各维度里展示的建议带上它对应的路线图编号和顺序。

    路线图里的一条建议可能由多个维度的建议合并而来，所以按“类别 + 解决的差距有交集”认领：
    维度里的每条建议都能找到唯一的路线图条目，单维度导出和展示据此保持一致。"""
    for r in results:
        for act in r["actions"]:
            overlap = [road for road in roadmap if set(road["addresses"]) & set(act["addresses"])]
            # 优先认领同类别的；同名建议跨类别合并后，退而认领有交集的那条
            target = next((r for r in overlap if r["category"] == act["category"]), None) or next(
                iter(overlap), None
            )
            if target is not None:
                act["id"], act["priority"] = target["id"], target["priority"]


def absorb_duplicates(
    actions: list[dict], groups: list[list[str]]
) -> tuple[list[dict], dict[str, str]]:
    """把每组里第一个以外的建议并入第一个，重新编号。

    返回 (新的建议列表, 旧编号 -> 新编号)。并入时保留较详细的动作/责任/完成标准、
    更紧的时限，关联差距取并集——每个差距都仍然有建议覆盖。
    调用方应已通过 validate_synthesis 校验过分组(编号存在、同类别、互不重叠)。"""
    by_id = {a["id"]: {**a, "addresses": list(a["addresses"])} for a in actions}
    absorbed_into: dict[str, str] = {}
    for group in groups:
        keep = group[0]
        for other in group[1:]:
            src, dst = by_id[other], by_id[keep]
            _absorb_into(dst, src)
            dst["addresses"] = list(dict.fromkeys([*dst["addresses"], *src["addresses"]]))
            absorbed_into[other] = keep
    kept = [by_id[a["id"]] for a in actions if a["id"] not in absorbed_into]
    remap = {}
    for n, act in enumerate(kept, 1):
        old = act["id"]
        act["id"], act["priority"] = f"A-{n:02d}", n
        remap[old] = act["id"]
    for old, target in absorbed_into.items():
        remap[old] = remap[target]
    return kept, remap


def _has_cycle(pairs: list[tuple[str, str]]) -> bool:
    graph: dict[str, list[str]] = {}
    for a, b in pairs:
        graph.setdefault(a, []).append(b)
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        state[node] = 1
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1 or (state.get(nxt) is None and visit(nxt)):
                return True
        state[node] = 2
        return False

    return any(state.get(n) is None and visit(n) for n in list(graph))


def finalize_roadmap(
    actions: list[dict], dependencies: list[tuple[str, str]]
) -> tuple[list[dict], list[dict]]:
    """给每条建议定阶段、挂上先后依赖，并按“阶段 → 依赖顺序 → 风险顺序”排好。

    阶段只由时限决定（程序算）。依赖来自结论页模型，且已通过校验；这里再做一次保险：
    合并建议时限会变，若出现“前提晚于后续”“自指”“成环”，丢弃该依赖并记录，不改动其他内容。
    返回 (排好序的路线图, 被丢弃的依赖记录)。"""
    by_id = {a["id"]: a for a in actions}
    for a in actions:
        a["phase"] = phase_of(a["due_in_days"])
        a["depends_on"] = []
    dropped: list[dict] = []
    kept: list[tuple[str, str]] = []
    for a, b in dict.fromkeys(dependencies):
        if a not in by_id or b not in by_id or a == b:
            dropped.append({"action": a, "after": b, "reason": "编号不存在或指向自身"})
        elif by_id[b]["phase"] > by_id[a]["phase"]:
            dropped.append({"action": a, "after": b, "reason": "前提安排在后续阶段"})
        elif _has_cycle([*kept, (a, b)]):
            dropped.append({"action": a, "after": b, "reason": "会形成循环依赖"})
        else:
            kept.append((a, b))
            by_id[a]["depends_on"].append(b)
    ordered: list[dict] = []
    for phase in (1, 2, 3):
        pending = [a for a in actions if a["phase"] == phase]
        done: set[str] = set()
        while pending:
            ready = next(
                (
                    a
                    for a in pending
                    if all(
                        d in done or d not in {p["id"] for p in pending} for d in a["depends_on"]
                    )
                ),
                pending[0],
            )
            pending.remove(ready)
            done.add(ready["id"])
            ordered.append(ready)
    for n, a in enumerate(ordered, 1):
        a["priority"] = n
    return ordered, dropped


_DEADLINE = re.compile(r"(\d+)\s*(?:个)?\s*(天|日|周|星期|个月|月)(?:内|以内|之内|完成|前|后)")
_OPERATIONAL_TARGET = re.compile(r"\d+\s*(?:个)?\s*(?:小时|分钟|工作日)(?:内|以内|之内)?")
_ANY_DURATION = re.compile(r"(?<!\d)(\d+)\s*(?:个)?\s*(天|日|周|个月)")
_DEADLINE_DAYS = {"天": 1, "日": 1, "周": 7, "星期": 7, "个月": 30, "月": 30}


def deadline_days(text: str) -> set[int]:
    """文字里“N 天内 / N 周内 / N 个月内”这类整改时限，折算成天数。"""
    return {int(n) * _DEADLINE_DAYS[u] for n, u in _DEADLINE.findall(text or "")}


def without_deadlines(text: str, allowed_days: set[int]) -> str:
    """把已被本方面建议时限认可的“N 天内”从文字里去掉，剩下的才按“数字必须有来源”检查。

    这是顾问给出的行动安排，不是法规里的数字，不需要出现在依据里；
    但必须和本方面某条建议的时限一致，所以凭空写“7 天内必须上报”这类法定期限仍会被拦。"""

    def drop(m: re.Match) -> str:
        days = int(m.group(1)) * _DEADLINE_DAYS[m.group(2)]
        return "" if days in allowed_days else m.group(0)

    return _DEADLINE.sub(drop, text or "")


def text_problems(
    label: str,
    raw: str,
    corpus: str,
    sources: list[str],
    allowed_days: set[int] | None = None,
    operational: bool = False,
) -> list[str]:
    """一段给读者看的文字的通用检查。先去掉加粗标记再做事实检查，加粗只做格式规范检查。

    allowed_days：本方面建议里写的时限（天）。文字里与之吻合的“N 天内”是行动安排，不查来源。
    operational：建议的“动作/完成标准”里的“24 小时内响应”“15 分钟内停用”是顾问设的运行指标，
    不是法规数字，不查来源；结论、理由等陈述性文字仍然要查（那里写“72 小时内通报”就是在说法律）。"""
    text = strip_bold(raw)
    if operational:
        text = _OPERATIONAL_TARGET.sub("", text)
    problems = bold_problems(label, raw)
    if _BRACKET_REF.search(text):
        problems.append(f"{label}不得自带[数字]角标，引用只通过 evidence_ids 表达")
    leaked = _INTERNAL_ID.findall(text) + [w for w in _SYSTEM_WORDS if w in text]
    if leaked:
        problems.append(
            f"{label}里出现了内部编号或系统词 {sorted(set(leaked))}，读者看不懂，请改成业务说法"
        )
    bad = unsupported_numbers(without_deadlines(text, allowed_days or set()), corpus)
    if bad:
        problems.append(
            f"{label}出现了证据和业务事实里都没有的数字 {bad}。法规数字必须来自依据；"
            "如果这是你建议的整改时限，请让它和本方面某条建议的 due_in_days 一致，否则删除或改写"
        )
    if copies_source(text, sources):
        problems.append(f"{label}大段照抄了依据原文，请用自己的话概括，不要摘抄")
    return problems


def validate_dimension(
    resp: DimensionResponse,
    *,
    evidence: dict[str, dict],
    facts: dict[str, Any],
    code: str = "",
) -> list[str]:
    """返回问题清单;空表示通过。问题文字会原样反馈给模型重写。"""
    problems: list[str] = []
    fact_text = " ".join(str(v) for v in facts.values())
    corpus = " ".join(
        [fact_text]
        + [
            " ".join(
                str(e.get(k) or "")
                for k in ("obligation", "quote_zh", "quote_original", "clause_number")
            )
            for e in evidence.values()
        ]
    )
    sources = [
        s for e in evidence.values() for s in (e.get("quote_zh"), e.get("quote_original")) if s
    ]

    # 本方面建议里写的整改时限；文字里与之吻合的“N 天内”是行动安排，不查来源
    own_days = {act.due_in_days for act in resp.actions}
    if code == ROADMAP_DIMENSION:
        # 路线图方面的总述会按阶段说“30 天内…60 天内…”，阶段边界是行动安排，不是法规数字
        own_days |= {30, 60}

    def text_checks(label: str, raw: str, operational: bool = False) -> None:
        if code == ROADMAP_DIMENSION:
            # 路线图方面整体就是在谈时限：“前 30 天”“30 天为限”等任何天数写法，只要与本方面
            # 某条建议的时限（或阶段边界）一致，都是行动安排，不是法规数字
            raw = _ANY_DURATION.sub(
                lambda m: (
                    "某" + m.group(2)
                    if int(m.group(1)) * _DEADLINE_DAYS[m.group(2)] in own_days
                    else m.group(0)
                ),
                raw,
            )
        problems.extend(text_problems(label, raw, corpus, sources, own_days, operational))

    text_checks("结论", resp.conclusion)
    if resp.overview:
        text_checks("概述", resp.overview)
    for i, a in enumerate(resp.assessments, 1):
        tag = f"差距{i}"
        unknown = [e for e in a.evidence_ids if e not in evidence]
        if unknown:
            problems.append(f"{tag}引用了不存在的依据 {unknown}，只能用输入中的 evidence_id")
        bad_keys = [k for k in a.business_fact_keys if k not in facts]
        if bad_keys:
            problems.append(f"{tag}引用了用户没有提供的业务事实 {bad_keys}")
        if a.status in ("compliant", "partial", "non_compliant"):
            if not a.business_fact_keys:
                problems.append(
                    f"{tag}判断为{STATUS_LABEL[a.status]}，但没有引用任何业务事实；"
                    "没有业务事实支撑时只能写 insufficient_info"
                )
            if not a.business_state.strip():
                problems.append(f"{tag}缺少业务现状描述")
        if a.status in ("partial", "non_compliant") and (a.likelihood is None or a.impact is None):
            problems.append(f"{tag}存在差距，必须给出 likelihood 和 impact（1-5）")
        if a.status == "insufficient_info":
            if not a.needs_fact.strip():
                problems.append(f"{tag}信息不足时必须写明需要用户补充什么（needs_fact）")
            if a.likelihood is not None or a.impact is not None:
                problems.append(f"{tag}信息不足时不要给 likelihood/impact")
        for field, value in (
            ("要求", a.requirement),
            ("现状", a.business_state),
            ("理由", a.rationale),
            ("需补充", a.needs_fact),
        ):
            if value:
                text_checks(f"{tag}{field}", value)

    scored = [
        (i, risk_level(a.likelihood, a.impact, a.status)[1])
        for i, a in enumerate(resp.assessments, 1)
    ]
    needs_action = {i for i, level in scored if level in ("high", "medium")}
    addressed: set[int] = set()
    for j, act in enumerate(resp.actions, 1):
        bad = [x for x in act.addresses if not 1 <= x <= len(resp.assessments)]
        if bad:
            problems.append(f"建议{j}关联了不存在的差距编号 {bad}")
        for x in act.addresses:
            if 1 <= x <= len(resp.assessments):
                if resp.assessments[x - 1].status in ("compliant", "insufficient_info"):
                    problems.append(f"建议{j}关联的差距{x}不是需要整改的差距")
                else:
                    addressed.add(x)
        for field, value in (
            ("标题", act.title),
            ("动作", act.action),
            ("责任角色", act.owner_role),
            ("完成标准", act.done_criteria),
        ):
            text_checks(f"建议{j}{field}", value, operational=field in ("动作", "完成标准"))
    missing = sorted(needs_action - addressed)
    if missing:
        problems.append(f"中高风险差距 {missing} 没有对应的建议")
    return problems


def validate_synthesis(
    resp: SynthesisResponse,
    *,
    action_ids: set[str],
    counts: dict[str, int],
    allowed_numbers: set[str],
    action_category: dict[str, str] | None = None,
    action_phase: dict[str, int] | None = None,
) -> list[str]:
    problems: list[str] = []
    if resp.verdict not in VERDICTS:
        problems.append(f"verdict 只能是 {list(VERDICTS)} 之一")
    pairs = [(d.action_id, d.after_action_id) for d in resp.dependencies]
    for a, b in pairs:
        if a not in action_ids or b not in action_ids:
            problems.append(f"dependencies 里有不存在的建议编号 {[a, b]}")
        elif a == b:
            problems.append(f"dependencies 里 {a} 不能依赖自己")
        elif action_phase and action_phase[b] > action_phase[a]:
            problems.append(
                f"dependencies 里 {b} 的时限比 {a} 更晚，不能作为 {a} 的前提；"
                "先后关系只能写在时限更早或相同阶段的建议之间"
            )
    if _has_cycle(pairs):
        problems.append("dependencies 里出现了循环依赖")
    bad_ids = [a for a in resp.must_do_action_ids if a not in action_ids]
    if bad_ids:
        problems.append(f"must_do_action_ids 里有不存在的建议编号 {bad_ids}")
    grouped: list[str] = []
    for group in resp.duplicate_action_groups:
        if len(group) < 2:
            problems.append("duplicate_action_groups 每组至少两个建议编号")
        unknown = [a for a in group if a not in action_ids]
        if unknown:
            problems.append(f"duplicate_action_groups 里有不存在的建议编号 {unknown}")
        # 不再要求类别相同：同一件事常被不同方面归入不同类别（如“云端”与“组织与流程”），
        # 合并后以保留的那条为准；不同的事不会被程序合并，是否成组由结论页模型判断
        grouped += group
    if len(grouped) != len(set(grouped)):
        problems.append("duplicate_action_groups 里同一个建议编号不能出现在多个分组")
    allowed = allowed_numbers | {str(v) for v in counts.values()}
    for label, raw in (("headline", resp.headline), ("summary", resp.summary)):
        text = strip_bold(raw)
        problems.extend(bold_problems(label, raw))
        if _BRACKET_REF.search(text):
            problems.append(f"{label}不得自带[数字]角标")
        leaked = _INTERNAL_ID.findall(text) + [w for w in _SYSTEM_WORDS if w in text]
        if leaked:
            problems.append(
                f"{label}里出现了内部编号或系统词 {sorted(set(leaked))}，请改成业务说法"
            )
        bad = sorted(n for n in numbers_in(text) if n not in allowed)
        if bad:
            problems.append(f"{label}里的数字 {bad} 与风险统计或建议时限对不上")
    return problems


def coerce_verdict(label: str, counts: dict[str, int]) -> tuple[str, str | None]:
    """结论标签必须和风险统计一致;不一致时由程序校正并记录原因。"""
    judged = counts["high"] + counts["medium"] + counts["low"]
    if judged == 0:
        return "信息不足", None if label == "信息不足" else "没有可判定的差距，按信息不足处理"
    if counts["high"] and label in ("可行",):
        return "有条件可行", "存在高风险差距，不能写“可行”，已校正"
    if not counts["high"] and not counts["medium"] and label in ("暂不建议",):
        return "可行", "没有中高风险差距，不能写“暂不建议”，已校正"
    return label, None


# ---------------------------------------------------------------- 提示词

DIMENSION_SYSTEM_PROMPT = (
    "你是汽车数据跨境合规咨询顾问，为车企业务负责人写一个维度的评估意见。"
    "只返回合法 JSON 对象。\n"
    "你拿到两样东西：business_facts（用户亲口提供的业务事实）和 evidence（程序已逐字核验过的法律依据，"
    "含中文结论 obligation 和机器翻译的 quote_zh）。你只能依据这两样写，不要引入其他法律或常识性规定。\n"
    "写法：像资深咨询顾问对客户说话——先给结论，再说差在哪、为什么要紧、先做什么；"
    "用连贯的中文，不要罗列条文，不要照抄依据原文，也不要写条号。"
    "这是整份报告里的一个章节，读者两三分钟要读完：宁可少而准，只写对这个客户真正要紧的几条。\n"
    "输入里的 dimension.style 决定写法：\n"
    "- explain（解释型）：先用 overview 写一两段话，向非专业读者讲清这个国家在本方面的框架或定义"
    "（谁管、管什么、关键概念是什么、对车联网数据意味着什么），再只写 1-3 条对该客户有影响的发现；"
    "conclusion 与 overview 分工不同、绝不能互相复述：overview 只讲当地框架本身，"
    "conclusion 只讲“对这个客户意味着什么、现状缺什么、先做什么”，不要再解释一遍框架；\n"
    "- assess（评估型）：overview 留空；直接写 2-4 条发现，每条对照客户的现状。\n"
    "硬性规则：\n"
    "1. 每个差距(assessments)的 evidence_ids 只能填输入里的 evidence_id；不要自己写 [数字] 角标。\n"
    "2. 业务现状只能来自 business_facts，用 business_fact_keys 指明依据；"
    "判断“已达到/部分达到/未达到”必须有业务事实支撑；没有业务事实就用 insufficient_info，"
    "并在 needs_fact 写清楚需要用户补充什么。\n"
    "3. likelihood（可能性）和 impact（影响）各打 1-5 分并写 rationale；不要自己写风险高低，由系统计算。\n"
    "4. 数字（金额、天数、人数、年限）只能是依据或业务事实里已有的数字。\n"
    "5. 每个差距都必须写 rationale（合规分析与法律论证，2-4句话、100-240字）：按“法定合规边界→客户业务与技术现状比对→必要性/最小侵害替代性或现有保护措施适当性与有效性分析→违规或权益受损风险传导路径”展开严密推演；即使是 insufficient_info 也要写清缺失该事实为何阻断合法性或相称性判定。\n"
    "6. 篇幅与论证深度：requirement 一句话不超过45字概括“这件事要做到什么”，不要整句转述条文；"
    "business_state 不超过130字，客观提炼和这条要求相关的现状；rationale 100-240字完整展开法理与实务论证；"
    "conclusion 3-5句话、180-340字，直接开头（不要写“结论：”前缀），先给出本方面明确合规研判意见，再从合法性基础、最小必要与相称性、保护措施适当性三个层次归纳核心差距根因与优先处置方向；"
    "assessments 最多5条，优先写最要紧的，不要为凑数而写。\n"
    "7. 状态为 insufficient_info 的差距不要打 likelihood/impact，也不要放进任何建议的 addresses；"
    "需要用户补充的内容只写在 needs_fact。\n"
    "8. 建议(actions)要能直接交给工程或法务团队：category 取 vehicle(车端)、data_architecture(数据架构)、"
    "cloud(云端)、organization(组织与流程) 之一；先写一个不超过16字的短标题(title，例如“签署跨境传输协议”)，"
    "再写动作、责任角色(owner_role)、建议时限(due_in_days，天数整数)、完成标准(done_criteria)；"
    "addresses 填它解决的差距编号(从1开始)。优先写车端、数据架构、云端的具体工程动作，"
    "不要全是“制定制度”“加强管理”这类泛泛的话。\n"
    "9. 每个可能性×影响较高的差距都要有建议。没有依据的维度不要硬写。\n"
    "9a. 如果提供的依据只针对另一类场景、不适用于当前业务，就如实说“这些依据不适用于当前业务，"
    "该方面还没有可用的依据”，assessments 留空；不要写“未发现差距”“没有问题”“已合规”这类话——"
    "没有依据不等于没有差距。\n"
    "10. 不要写“根据《某某法》第几条”这类话，法条由系统在文末以参考文献形式呈现。\n"
    "11. 读者是业务负责人，不是法律或系统人员：不要在任何文字里出现 D1、D2…、F-01、A-01 这类内部编号，"
    "也不要写“证据”“evidence_id”“维度编号”“原子规则”这类系统词；用业务人员听得懂的话。\n"
    "12. 在 conclusion、business_state、rationale、action、done_criteria 里，用 **双星号** 把最关键的词句包起来表示加粗："
    "每一段最多加粗两三处，每处是一个短语（例如“**每天向中国批量回传未脱敏图像**”“**30 天内签署传输协议**”），"
    "不要整句加粗，加粗部分合计不要超过全文的三分之一。只允许这一种标记，不要用其他 Markdown 或 HTML。\n"
    "13. 这份报告共有八个方面，其他方面会各自写自己的内容：只写本方面独有的内容，"
    "不要重复说明其他方面的主题（例如数据怎么分类、传输要什么手续、罚则多重、市场准入怎么办）；"
    "同一个客户现状只在最相关的一条差距里写，不要在每条里重复整段背景。\n"
    '输出格式：{"conclusion":"...","overview":"","assessments":[{"requirement":"...","evidence_ids":["F-01"],'
    '"business_fact_keys":["..."],"business_state":"...","status":"partial","likelihood":4,'
    '"impact":4,"rationale":"...","needs_fact":""}],"actions":[{"category":"organization",'
    '"title":"...","action":"...","owner_role":"...","due_in_days":30,"done_criteria":"...",'
    '"addresses":[1]}]}'
)

SYNTHESIS_SYSTEM_PROMPT = (
    "你是汽车数据跨境合规咨询顾问，为车企负责人写整份评估的结论页。只返回合法 JSON 对象。\n"
    "输入是各维度的结论、风险统计和已定稿的建议清单。请给出：\n"
    "- verdict：可行 / 有条件可行 / 暂不建议 / 信息不足 之一；\n"
    "- headline：一句话回答“这件事能不能做、核心前提或阻断点在哪”，不超过65字；\n"
    "- summary：4-6句话（220-450字），给出严谨的结论性研判结果：一是明确总体裁断意见及合法性、必要性基础；二是按“消除＞降低＞转移＞接受”处置原则说明核心中高风险的压降路径（明确高风险未压降至中低区间前不得带病放行）；三是列明正式启动传输或上线前必须闭环的刚性前置条件，以及单路径未获批或异常时单独暂停并切换本地处理预案的隔离机制；不要罗列条文；\n"
    "- must_do_action_ids：从输入的建议编号里挑 1-3 个最该先做的；\n"
    "- duplicate_action_groups：不同维度各自提出的、实质上是同一件事的建议（例如都是“重做告知同意流程”），"
    "把它们的编号分成一组，保留的是组内第一个（选写得最完整的放在第一个）。判断标准是“做完其中一条，另一条是不是也就做完了”："
    "类别不同、时限不同、标题措辞不同都不影响判断，例如“签署跨境传输协议”和“签署跨境传输安排”、"
    "“出境前脱敏与最小化”和“车端图像脱敏后再回传”和“关停未脱敏图像回传”，都是同一件事，必须成组；"
    "但“盘点接收方”和“签署协议”是先后两步，不是重复。先逐条检查有没有这样的重复，没有才返回空数组；\n"
    "- dependencies：整改的先后关系。只有“不先做 A 就没法做 B”才写，例如先做数据分类清单，"
    "才能决定哪些数据能出境；格式是 {action_id: B, after_action_id: A}。A 的时限必须不晚于 B，"
    "不要成环，没有就返回空数组，不要为了好看而硬凑。\n"
    "规则：不得引入输入之外的事实；数字只能是风险统计或建议时限里已有的数字；不要自己写 [数字] 角标；"
    "不要出现 D1、A-01 这类内部编号，读者是业务负责人。headline 和 summary 里用 **双星号** 把最关键的词句包起来加粗，"
    "每段最多两三处、每处是短语，不要整句加粗，只允许这一种标记。\n"
    '输出格式：{"verdict":"有条件可行","headline":"...","summary":"...","must_do_action_ids":["A-01"],'
    '"duplicate_action_groups":[["A-02","A-04"]],'
    '"dependencies":[{"action_id":"A-03","after_action_id":"A-01"}]}'
)


# ---------------------------------------------------------------- 证据账本


def build_ledger(dimension: dict) -> dict[str, dict]:
    """从一个维度的已核验 findings/citations 构造 {evidence_id: 证据}。"""
    cites = {c.get("clause_id"): c for c in dimension.get("citations", []) or []}
    ledger: dict[str, dict] = {}
    for f in dimension.get("findings", []) or []:
        fid = f.get("finding_id")
        if not fid:
            continue
        c = cites.get(f.get("clause_id")) or {}
        ledger[fid] = {
            "evidence_id": fid,
            "clause_id": f.get("clause_id"),
            "version_id": f.get("version_id"),
            "document_title": f.get("document_title") or c.get("document_title") or "",
            "clause_number": f.get("clause_number") or c.get("clause_number") or "",
            "version_no": f.get("version_no") or c.get("version_no") or 1,
            "source_url": f.get("source_url") or c.get("source_url") or "",
            "obligation": f.get("compliance_obligation") or "",
            "quote_original": f.get("basis_quote") or c.get("quote_original") or "",
            "quote_zh": f.get("basis_zh") or c.get("quote_zh") or "",
            "risk_hint": f.get("risk_level") or "",
            "review_status": c.get("review_status"),
            "source_review_status": c.get("source_review_status"),
            "translation_review_status": c.get("translation_review_status"),
            "source_notice": f.get("source_notice") or c.get("source_notice") or "",
            "language": c.get("language"),
        }
    return ledger


def _evidence_payload(ledger: dict[str, dict]) -> list[dict]:
    return [
        {
            "evidence_id": e["evidence_id"],
            "obligation": e["obligation"],
            "quote_zh": e["quote_zh"],
            "source": f"{e['document_title']} {e['clause_number']}".strip(),
        }
        for e in ledger.values()
    ]


# ---------------------------------------------------------------- 参考文献编号


def assign_references(consulting: dict, ledgers: dict[str, dict]) -> dict:
    """按阅读顺序给条款分配角标编号(同一条款只一个号)。可对已筛选的副本重复调用。"""
    order: dict[tuple, int] = {}
    refs: list[dict] = []

    def number_for(ev: dict, said: str = "") -> int:
        key = (ev.get("clause_id"), ev.get("version_id"))
        if key not in order:
            order[key] = len(refs) + 1
            refs.append(
                {
                    "n": order[key],
                    **{
                        k: ev.get(k)
                        for k in (
                            "clause_id",
                            "version_id",
                            "document_title",
                            "clause_number",
                            "version_no",
                            "source_url",
                            "quote_original",
                            "quote_zh",
                            "review_status",
                            "source_review_status",
                            "translation_review_status",
                            "source_notice",
                            "language",
                        )
                    },
                    "evidence_ids": [],
                    "points": [],
                    "summary": "",
                }
            )
        r = refs[order[key] - 1]
        if ev["evidence_id"] not in r["evidence_ids"]:
            r["evidence_ids"].append(ev["evidence_id"])
        if ev.get("obligation") and ev["obligation"] not in r["points"]:
            r["points"].append(ev["obligation"])
        # 参考文献条目的一句话说明：用咨询层为这条发现写的“要求”（自己的话、一句完整表述），
        # 取第一次引用到它的那条；比证据里整条义务的转述短得多，也不会被截成半句
        if said and not r["summary"]:
            r["summary"] = strip_bold(said).strip()
        return order[key]

    by_assessment = {a["id"]: a for d in consulting["dimensions"] for a in d["assessments"]}
    for top in consulting.get("top_risks", []):
        a = by_assessment.get(top["assessment_id"])
        top["ref_numbers"] = sorted(
            {
                number_for(ledgers[e], (a or {}).get("requirement", ""))
                for e in (a or {}).get("evidence_ids", [])
                if e in ledgers
            }
        )
    for d in consulting["dimensions"]:
        for a in d["assessments"]:
            a["ref_numbers"] = sorted(
                {
                    number_for(ledgers[e], a.get("requirement", ""))
                    for e in a.get("evidence_ids", [])
                    if e in ledgers
                }
            )
    consulting["references"] = refs
    return consulting


def filter_dimension(consulting: dict, code: str, ledgers: dict[str, dict]) -> dict:
    """单维度导出：只保留该维度，并按本文件内的阅读顺序重新编号。"""
    import copy

    c = copy.deepcopy(consulting)
    c["dimensions"] = [d for d in c["dimensions"] if d["code"] == code]
    keep = {a["id"] for d in c["dimensions"] for a in d["assessments"]}
    c["top_risks"] = [t for t in c.get("top_risks", []) if t["assessment_id"] in keep]
    # 路线图里的建议可能由多个维度的建议合并而来：只要它解决本维度的某个差距就保留
    # 落地方面本身就是路线图：单独导出这个方面时带上完整路线图
    if code != ROADMAP_DIMENSION:
        c["roadmap"] = [x for x in c.get("roadmap", []) if keep & set(x["addresses"])]
    return assign_references(c, ledgers)


# ---------------------------------------------------------------- 编排

LlmCall = Callable[..., Awaitable[LlmResult]]


def consulting_budget(config: dict[str, Any]) -> float:
    """最坏耗时：8 个维度分两批、每批最多重试一次，再加结论页的重试。"""
    return float(config["timeout"]) * (2 * MAX_ATTEMPTS + MAX_ATTEMPTS)


async def _ask(
    llm: LlmCall,
    system: str,
    payload: dict,
    schema: type[BaseModel],
    validate: Callable[[Any], list[str]],
    *,
    model: str,
    temperature: float,
    timeout: float,
) -> tuple[Any, int, list[str]]:
    """带校验反馈的重试。返回 (解析结果, token, 最后一轮问题)。失败时结果为 None。"""
    tokens = 0
    problems: list[str] = []
    feedback: list[str] | None = None
    for _ in range(MAX_ATTEMPTS):
        body = dict(payload)
        if feedback:
            body["must_fix"] = feedback
        res = await llm(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
            ],
            model=model,
            temperature=temperature,
            timeout=timeout,
        )
        tokens += res.total_tokens
        try:
            parsed = schema.model_validate(res.data)
        except ValidationError as exc:
            problems = ["输出格式不符合要求：" + str(exc.errors(include_input=False))[:600]]
            feedback = problems
            continue
        problems = validate(parsed)
        if not problems:
            return parsed, tokens, []
        feedback = problems
    return None, tokens, problems


async def build_consulting(
    *,
    dimensions: list[dict],
    facts: dict[str, Any],
    scenario: dict[str, str],
    config: dict[str, Any],
    llm: LlmCall = call_llm_json,
) -> dict:
    """生成咨询层。不抛出模型失败:失败的维度/整体会如实标记,由调用方决定如何交付。

    dimensions: 证据版报告的维度列表(含 findings、citations)。
    config: 需要 model、temperature、timeout。
    """
    model, temperature, timeout = config["model"], config["temperature"], config["timeout"]
    ledgers: dict[str, dict] = {}
    for d in dimensions:
        ledgers.update(build_ledger(d))

    sem = asyncio.Semaphore(4)
    total_tokens = 0
    rejected: list[dict] = []

    async def one(d: dict) -> dict:
        nonlocal total_tokens
        code, title = d["dimension_code"], d["dimension_title"]
        ledger = build_ledger(d)
        base = {
            "code": code,
            "title": title,
            "conclusion": "",
            "overview": "",
            "status": "ok",
            "note": "",
            "assessments": [],
            "actions": [],
        }
        if not ledger:
            base.update(
                status="no_evidence",
                conclusion="目前没有经过核验的依据可以支撑这个方面的判断，先不下结论。",
                note=d.get("missing_gap_note") or "依据缺口",
            )
            return base
        payload = {
            "dimension": {
                "code": code,
                "title": title,
                "style": "explain" if code in EXPLAIN_DIMENSIONS else "assess",
            },
            "scenario": scenario,
            "business_facts": facts,
            "evidence": _evidence_payload(ledger),
        }
        try:
            async with sem:
                parsed, used, problems = await _ask(
                    llm,
                    DIMENSION_SYSTEM_PROMPT,
                    payload,
                    DimensionResponse,
                    lambda r: validate_dimension(r, evidence=ledger, facts=facts, code=code),
                    model=model,
                    temperature=temperature,
                    timeout=timeout,
                )
        except LlmUnavailableError as exc:
            total_tokens += exc.tokens
            rejected.append({"dimension": code, "problems": [f"模型不可用：{exc.kind}"]})
            base.update(status="failed", note="这个方面的咨询意见暂时没有生成，可以重新评估")
            return base
        total_tokens += used
        if parsed is None:
            rejected.append({"dimension": code, "problems": problems})
            base.update(status="failed", note="这个方面的咨询意见暂时没有生成，可以重新评估")
            return base
        base["conclusion"] = parsed.conclusion.strip()
        base["overview"] = parsed.overview.strip()
        for i, a in enumerate(parsed.assessments, 1):
            score, level = risk_level(a.likelihood, a.impact, a.status)
            base["assessments"].append(
                {
                    "id": f"{code}-{i}",
                    "requirement": a.requirement.strip(),
                    "business_state": a.business_state.strip(),
                    "business_fact_keys": a.business_fact_keys,
                    "status": a.status,
                    "likelihood": a.likelihood,
                    "impact": a.impact,
                    "score": score,
                    "level": level,
                    "rationale": a.rationale.strip(),
                    "needs_fact": a.needs_fact.strip(),
                    "evidence_ids": a.evidence_ids,
                    "ref_numbers": [],
                }
            )
        for act in parsed.actions:
            base["actions"].append(
                {
                    "category": act.category,
                    "title": act.title.strip(),
                    "action": act.action.strip(),
                    "owner_role": act.owner_role.strip(),
                    "due_in_days": act.due_in_days,
                    "done_criteria": act.done_criteria.strip(),
                    "addresses": [f"{code}-{x}" for x in act.addresses],
                }
            )
        return base

    results = await asyncio.gather(*[one(d) for d in dimensions])

    # 建议编号与路线图
    by_level = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    level_of = {a["id"]: a["level"] for r in results for a in r["assessments"]}
    all_actions = merge_overlapping_actions(
        merge_similar_actions(merge_same_title([act for r in results for act in r["actions"]]))
    )
    all_actions.sort(
        key=lambda x: (
            -max((by_level[level_of.get(t, "unknown")] for t in x["addresses"]), default=0),
            x["due_in_days"],
        )
    )
    for n, act in enumerate(all_actions, 1):
        act["id"] = f"A-{n:02d}"
        act["priority"] = n
    link_dimension_actions(results, all_actions)

    counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for r in results:
        for a in r["assessments"]:
            counts[a["level"]] += 1

    # 风险排序(只列需要整改的)
    ranked = sorted(
        (a for r in results for a in r["assessments"] if a["level"] in ("high", "medium")),
        key=lambda a: (-(a["score"] or 0), a["id"]),
    )
    # 同一批整改动作就能解决的风险，说的是同一个根因，只保留分数最高的一条，
    # 否则风险排序里“跨境传输”会连出三四条近似的话
    fixes = {
        a["id"]: frozenset(x["id"] for x in all_actions if a["id"] in x["addresses"])
        for a in ranked
    }
    chosen: list[dict] = []
    for a in ranked:
        covered = fixes[a["id"]]
        # 整改动作重合过半（任一方向）就当作同一个根因
        if covered and any(
            len(covered & fixes[c["id"]]) / len(covered | fixes[c["id"]]) >= 0.5 for c in chosen
        ):
            continue
        chosen.append(a)
    top_risks = [
        {
            "assessment_id": a["id"],
            "title": a["requirement"],
            "level": a["level"],
            "score": a["score"],
        }
        for a in chosen[:5]
    ]

    consulting: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "status": "ok",
        "failure_reason": "",
        "verdict": {"label": "信息不足", "headline": "", "summary": "", "must_do_action_ids": []},
        "risk_counts": counts,
        "top_risks": top_risks,
        "dimensions": results,
        "roadmap": all_actions,
        "references": [],
        "model": model,
        "tokens": 0,
        "validation": {"rejected": rejected, "verdict_note": None},
    }

    failed_dims = [r["code"] for r in results if r["status"] == "failed"]
    failed_titles = [r["title"] for r in results if r["status"] == "failed"]
    usable = [r for r in results if r["status"] == "ok"]
    if not usable:
        consulting["status"] = "failed"
        consulting["failure_reason"] = (
            "各个方面的咨询意见都没有生成，可以重新评估" if failed_dims else "没有可用的已核验依据"
        )
        consulting["tokens"] = total_tokens
        return assign_references(consulting, ledgers)
    if failed_dims:
        consulting["status"] = "partial"
        consulting["failure_reason"] = "以下方面的咨询意见暂时没有生成：" + "、".join(failed_titles)

    # 结论页
    allowed_numbers = {str(a["due_in_days"]) for a in all_actions}
    synth_payload = {
        "scenario": scenario,
        "risk_counts": counts,
        "dimensions": [
            {
                "code": r["code"],
                "title": r["title"],
                "conclusion": r["conclusion"],
                "status": r["status"],
            }
            for r in results
        ],
        "actions": [
            {
                "action_id": x["id"],
                "title": x["title"],
                "action": x["action"],
                "due_in_days": x["due_in_days"],
                "category": x["category"],
                "addresses_levels": sorted({level_of.get(t, "unknown") for t in x["addresses"]}),
            }
            for x in all_actions
        ],
    }
    action_ids = {x["id"] for x in all_actions}
    action_phase = {x["id"]: phase_of(x["due_in_days"]) for x in all_actions}
    synth = None
    if all_actions:
        try:
            synth, used, problems = await _ask(
                llm,
                SYNTHESIS_SYSTEM_PROMPT,
                synth_payload,
                SynthesisResponse,
                lambda r: validate_synthesis(
                    r,
                    action_ids=action_ids,
                    counts=counts,
                    allowed_numbers=allowed_numbers,
                    action_category={x["id"]: x["category"] for x in all_actions},
                    action_phase=action_phase,
                ),
                model=model,
                temperature=temperature,
                timeout=timeout,
            )
            total_tokens += used
            if synth is None:
                rejected.append({"dimension": "summary", "problems": problems})
        except LlmUnavailableError as exc:
            total_tokens += exc.tokens
    deps = [(d.action_id, d.after_action_id) for d in synth.dependencies] if synth else []
    if synth is not None:
        label, note = coerce_verdict(synth.verdict, counts)
        must_do = list(synth.must_do_action_ids)
        if synth.duplicate_action_groups:
            all_actions, remap = absorb_duplicates(all_actions, synth.duplicate_action_groups)
            must_do = list(dict.fromkeys(remap[a] for a in must_do))
            deps = [(remap[a], remap[b]) for a, b in deps]
            consulting["validation"]["merged_action_groups"] = synth.duplicate_action_groups
        all_actions, dropped = finalize_roadmap(all_actions, deps)
        consulting["validation"]["dropped_dependencies"] = dropped
        link_dimension_actions(results, all_actions)
        consulting["roadmap"] = all_actions
        consulting["verdict"] = {
            "label": label,
            "headline": synth.headline.strip(),
            "summary": synth.summary.strip(),
            "must_do_action_ids": must_do,
        }
        consulting["validation"]["verdict_note"] = note
    else:
        # 总结页没通过时由程序给出保守版本，不让模型的未校验文字进入结论页
        all_actions, dropped = finalize_roadmap(all_actions, [])
        link_dimension_actions(results, all_actions)
        consulting["roadmap"] = all_actions
        label, _ = coerce_verdict(
            "有条件可行" if counts["high"] or counts["medium"] else "可行", counts
        )
        consulting["verdict"] = {
            "label": label,
            "headline": "已完成逐项分析，结论页文字未生成，请先看下面的风险排序和建议。",
            "summary": (
                f"本次共评估出高风险 {counts['high']} 项、中风险 {counts['medium']} 项、"
                f"低风险 {counts['low']} 项、需补充信息 {counts['unknown']} 项。"
                "建议按路线图的先后顺序处理。"
            ),
            "must_do_action_ids": [x["id"] for x in all_actions[:3]],
        }
        if consulting["status"] == "ok":
            consulting["status"] = "partial"
            consulting["failure_reason"] = (
                "总体结论的文字没有生成，已改用根据风险统计自动整理的简要说明"
            )
    consulting["roadmap_intro"] = roadmap_intro(consulting["roadmap"])
    consulting["conclusive_matrix"] = build_conclusive_matrix(consulting)
    consulting["tokens"] = total_tokens
    return assign_references(consulting, ledgers)


def roadmap_intro(roadmap: list[dict]) -> str:
    """程序按阶段数量写的一句话（不含模型文字），用作落地方面没有咨询意见时的开场。"""
    if not roadmap:
        return "目前没有需要安排的整改动作。"
    parts = []
    for phase in (1, 2, 3):
        n = sum(1 for a in roadmap if a.get("phase") == phase)
        if n:
            parts.append(f"{PHASE_LABELS[phase].split('：')[1]}{n} 项")
    return f"共 {len(roadmap)} 项整改动作，按先后分阶段推进：" + "，".join(parts) + "。"


# ---------------------------------------------------------------- Markdown(兼容旧展示与导出)


def _refs(nums: list[int]) -> str:
    return "".join(f"[{n}]" for n in nums)


def render_dimension_markdown(dim: dict, roadmap: list[dict] | None = None) -> str:
    """把一个方面的咨询内容写成可在线修订的纯文字。落地方面会把阶段路线图一并写出。"""
    lines = [dim["conclusion"].strip()]
    if dim.get("overview"):
        lines.append(dim["overview"].strip())
    if dim.get("code") == ROADMAP_DIMENSION and roadmap:
        for phase in (1, 2, 3):
            acts = [a for a in roadmap if a.get("phase") == phase]
            if acts:
                lines.append(PHASE_LABELS[phase])
                for a in acts:
                    lines.append(
                        f"- {a['title']}：{a['action']}（{a['owner_role']}；{a['due_in_days']}天内；"
                        f"完成标准：{a['done_criteria']}）"
                    )
        return "\n".join(lines)
    for a in dim["assessments"]:
        if a["status"] == "insufficient_info":
            lines.append(
                f"- {a['requirement']}{_refs(a['ref_numbers'])}：需要先补充——{a['needs_fact']}"
            )
        else:
            lines.append(
                f"- {a['requirement']}{_refs(a['ref_numbers'])}"
                f"（{STATUS_LABEL[a['status']]}，风险{LEVEL_LABEL[a['level']]}）：{a['rationale']}"
            )
    if dim["actions"]:
        lines.append("建议：")
        for x in dim["actions"]:
            lines.append(
                f"- {x['action']}（{CATEGORY_LABEL[x['category']]}；{x['owner_role']}；"
                f"{x['due_in_days']}天内；完成标准：{x['done_criteria']}）"
            )
    return "\n".join(lines)


def render_summary_text(consulting: dict) -> str:
    v = consulting["verdict"]
    return f"{v['label']}：{v['headline']}\n{v['summary']}"

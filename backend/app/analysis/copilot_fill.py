# ruff: noqa: E501
"""对话联动填报引擎（方案甲：预览卡片 + 勾选 + 一键填入 + 一键撤回）。

覆盖全平台三处填报入口：
1. 合规评估项目页（DocProjects.vue 右侧项目对话）：识别流向信息与 35+ 项填写项（含单选、条件补充、其他项、风险行）；
2. 各国官方表格填写页（Forms.vue 右侧填表助手）：中文解答栏位含义，并按官方表格语种（法语/英语/阿拉伯语/中文）提取对应栏位拟填值（带中文核对说明）；
3. 海外属地准入项目详情页（Admission.vue 右侧填报助手）：解答准入事项与材料要求，并提取“需要的信息”各栏拟填值。

所有拟填项均严守零捏造底线：
- 每项必须附带用户原话逐字摘抄（quote），逐字比对通过才进入预览卡片；
- 文本栏位不得出现用户原话与选项以外的任何数字、日期或编号；
- 不自动改写数据库，一律交给前端预览卡片由用户勾选确认后一键写入，并保留原值供一键撤回。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.analysis import risk_items
from app.analysis.chat_advisor import PRIVACY, PRIVACY_ASK
from app.analysis.doc_suggest import REMARK
from app.analysis.document_narrative import _numbers
from app.analysis.llm import call_llm_json
from app.analysis.wording import items_not_questions

CLAIMED = re.compile(r"已(经)?(帮您|帮你|为您|为你)?(填写|填好|填入|保存|记录|更新|写入)")
PURE_QUESTION = re.compile(r"(怎么填|如何填|什么意思|是什么|哪条|哪些|为什么|要不要|需不需要|区别|要求是什么|需要交什么)")
FACT_SIGNAL = re.compile(
    r"(我们|我司|本公司|公司|企业|主体|车型|车系|传|发往|保存|存储|期限|年|月|日|人|万|条|没有|不涉及|不含|无|否|是|选|改为|改成|换成|修改|填成|设为|邮箱|电话|地址|登记|注册|税号|负责人|DPO|加密|脱敏|合同|认证|风险|@|\d)",
    re.I,
)

LANG_NAME = {
    "fr": "法语（Français）",
    "en": "英语（English）",
    "ar": "阿拉伯语（العربية）",
    "zh": "中文",
}

DOC_BATCH = 9
FORM_BATCH = 45

DOC_SYSTEM = (
    "你在协助企业通过对话填写跨境数据合规评估项目。请只依据【用户当前输入】（必要时结合【最近对话】判断指代哪一栏）提取可以填入左侧栏目的值。\n"
    "提取规则：\n"
    "1. 只提取用户在【用户当前输入】中明确说出的事实、否定回答（如“没有/不涉及/否”）或明确要求修改的栏目；用户纯提问、未给出自身情况的栏目绝不输出。\n"
    "2. quote 必须逐字摘抄【用户当前输入】中支撑该项的原话片段（不改字、不省略）；拿不出原话依据的绝不输出。\n"
    "3. 选择项处理：\n"
    "   - 若栏目有 choices 且无 detail_on：优先匹配 choices 中的选项；若 other=true 且不在选项中，可直接写用户的实际内容。\n"
    "   - 若栏目有 choices 且有 detail_on（例如 choices=[\"是\",\"否\"]，detail_on=\"是\"）：当用户属于该项且说明了具体情况时，value 写成“是，具体情况”（中文逗号连接）；若用户只说了否定，value 写对应的否定选项（如“否”或“无”）。\n"
    "4. 风险行栏目（kind=\"risks\"，即 risk_rating）：当用户描述具体合规风险时，在 risk_row 对象中输出 {\"risk\":\"风险内容\",\"likelihood\":\"低|中|高|空\",\"severity\":\"低|中|高|空\",\"measures\":\"应对措施\",\"owner\":\"责任人\",\"due\":\"完成期限\"}，只填用户说过的字段，没说的留空字符串，value 留空即可。\n"
    "5. 已填栏目（current 非空）：仅当用户在当前输入中明确给出新信息或要求修改该栏时才输出，不要重复输出未提及的已填值。\n"
    "6. 严禁编造任何数字、日期、人名、公司名、注册号、联系方式；严禁把栏目的示例当成用户事实；value 中不得写“未提及/待补充/说明未说明”等评语。\n"
    "7. 如输入中出现形如 __CYSH_SECRET_...__ 的保护占位符，代表公司名、邮箱、电话或证件号等敏感信息，请根据上下文判断归属栏目，并在 value 与 quote 中原样完整保留该占位符（含前后双下划线，一字不改）。\n"
    "只输出 JSON：{\"proposals\":[{\"key\":\"...\",\"value\":\"...\",\"quote\":\"...\",\"risk_row\":null}]}"
)

FORM_EXTRACT_SYSTEM = (
    "你在协助企业填写外国监管机构的官方申报原表。请根据【表格信息】与【本批栏位列表】，从【用户当前输入】（结合【最近对话】确定指代）中提取可填入栏位的值。\n"
    "提取规则：\n"
    "1. 只提取用户在【用户当前输入】中明确说出的事实、选择或修改指令；纯提问绝不生成 proposal。\n"
    "2. quote 必须逐字摘抄【用户当前输入】中的原话片段。\n"
    "3. 按栏位 kind 输出 value：\n"
    "   - kind=\"text\"：value 必须使用【表格填写语种】书写（例如表格语种为法语 fr 时把中文事实准确译为规范法语；英语 en 译为规范英语；阿拉伯语 ar 译为规范阿拉伯语；专有型号、邮箱、电话、登记号保留原字符）。如栏位有 max_chars 字数上限，译文须精炼控制在该字数以内！同时在 note_zh 用简短中文写明所填含义（如“中文核对：……”；若表格本身是中文则 note_zh 留空）。\n"
    "   - kind=\"radio\"：value 必须严格填写该栏 options 中的某一个选项键名（字符串），不得自造键名。\n"
    "   - kind=\"check\"：value 必须填写由该栏 options 中选项键名组成的字符串数组（如 [\"opt1\"]）。\n"
    "4. 严禁编造任何用户未提及的数字、日期、编号、地址、机构或人名。\n"
    "5. 如输入中出现形如 __CYSH_SECRET_...__ 的保护占位符，在 value、quote、note_zh 中均须原样完整保留（含前后双下划线，翻译外文时也绝不改动该占位符）。\n"
    "只输出 JSON：{\"proposals\":[{\"key\":\"栏位id\",\"value\":...,\"note_zh\":\"...\",\"quote\":\"...\"}]}"
)

FORM_REPLY_SYSTEM = (
    "你是官方申报原表填写页右侧的填表助手。请围绕【表格信息】与【栏位概览】用自然、口语化的中文回复用户（2 至 4 句，不用标题和列表，不说“题/本题/题目”，一律称“这一栏”或“填写项”）。\n"
    "回复准则：\n"
    "1. 若用户在问某栏怎么填、什么意思、填什么语种：用通俗中文解释该栏含义、官方原表要求的语种和写法；举例时必须标明“例如”。\n"
    "2. 若用户在陈述企业/业务情况或给出某栏答案：简要回应已识别出对应栏位，提示用户核对气泡下方的预览卡片并点击“一键填入”即可写入左侧表格（严禁宣称“已经帮你填好/已保存”）。\n"
    "3. 严禁编造法规条号、罚款数额或用户未说的数字；如出现形如 __CYSH_SECRET_...__ 的占位符必须原样保留。\n"
    "只输出 JSON：{\"reply\":\"...\"}"
)

ADMISSION_SYSTEM = (
    "你是海外属地准入办理页右侧的填报助手，负责解答该准入事项的材料要求，并从【用户当前输入】（结合【最近对话】）中提取可填入左侧“需要的信息”栏位的值。\n"
    "规则：\n"
    "1. reply 用简洁自然的中文（2 至 4 句，不用标题和列表，不说“题”字）：\n"
    "   - 用户询问准入事项、受理机构、办理环节或某栏怎么填时，依据【准入项目概况】与【栏位列表】准确解答；举例须标明“例如”，不把示例放进 proposals。\n"
    "   - 用户提供企业、联系人、登记号、车型或业务信息时，简要复述理解，提示核对下方卡片后点“一键填入”即可写入（严禁宣称“已经帮你填好/已保存”）。\n"
    "2. proposals 提取规则：\n"
    "   - 只提取【用户当前输入】里明确给出的事实或修改项，每项必须附带逐字摘抄的 quote。\n"
    "   - key 必须来自【栏位列表】。\n"
    "   - 电子邮箱栏只填邮箱地址本身，电话/传真栏只填号码本身（含区号/+号），登记号/税号栏只填编号本身，不夹带多余说明词。\n"
    "   - 严禁编造任何数字、编号、邮箱、电话或公司名。\n"
    "   - 如输入中出现形如 __CYSH_SECRET_...__ 的保护占位符，在 reply、value、quote 中必须原样完整保留（含前后双下划线，一字不改）。\n"
    "只输出 JSON：{\"reply\":\"...\",\"proposals\":[{\"key\":\"...\",\"value\":\"...\",\"quote\":\"...\"}]}"
)


def _flat(text: str) -> str:
    return re.sub(r"[\s\u3000，,。；;：:、！？!?“”\"'‘’（）()【】\[\]《》<>·—\-]+", "", (text or "").lower())


def _quote_matches(quote: str, message: str) -> bool:
    fq = _flat(quote)
    fm = _flat(message)
    return bool(fq and fm and fq in fm)


def _decomma_numbers(s: str) -> set[str]:
    decomma = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s or "")
    return _numbers(decomma)


def _allowed_numbers(s: str) -> set[str]:
    return _numbers(s) | _decomma_numbers(s)


def _trim_history(history: list[dict[str, Any]] | None, limit: int = 6) -> list[dict[str, str]]:
    if not history:
        return []
    out: list[dict[str, str]] = []
    for item in history[-limit:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        text = str(item.get("text") or item.get("content") or "").strip()
        if role in ("user", "assistant") and text:
            out.append({"role": role, "text": text[:300]})
    return out


def is_pure_short_question(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return True
    if len(text) < 38 and (text.endswith(("?", "？")) or PURE_QUESTION.search(text)) and not FACT_SIGNAL.search(text):
        return True
    return False


def _format_doc_choice(q: dict[str, Any], raw_val: str) -> str:
    choices = list(q.get("choices") or [])
    val = raw_val.strip()
    if not choices or not val:
        return val
    if val in choices:
        return val
    detail_on = str(q.get("detail_on") or "")
    if detail_on and val.startswith(detail_on):
        rest = val[len(detail_on) :].lstrip("，,：:、 ")
        return f"{detail_on}，{rest}" if rest else detail_on
    for ch in choices:
        if val == ch:
            return ch
    if q.get("other"):
        return val
    # 若模型给出了否定近义词，归一到选项中的否定项
    if val in ("否", "无", "没有", "不涉及", "不含"):
        for ch in choices:
            if ch in ("否", "无", "不含", "没有", "不涉及"):
                return ch
    return val


async def _ask_doc_batch(
    fields: list[dict[str, Any]],
    route_ctx: dict[str, str],
    message: str,
    recent: list[dict[str, str]],
) -> list[dict[str, Any]]:
    payload = {
        "业务概况": route_ctx,
        "最近对话": recent,
        "用户当前输入": message,
        "可填栏目": fields,
    }
    res = await call_llm_json(
        [{"role": "system", "content": DOC_SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        max_tokens=4000,
        temperature=0.1,
    )
    data = res.data if isinstance(res.data, dict) else {}
    items = data.get("proposals")
    return list(items) if isinstance(items, list) else []


async def extract_doc_project_proposals(
    view: dict[str, Any],
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract interactive fill proposals for DocProjects.vue from a user chat message."""
    text = (message or "").strip()
    if not text or PRIVACY_ASK.search(text) or is_pure_short_question(text):
        return []

    route = view.get("route") or {}
    route_ctx = {
        "数据发出国": str(route.get("source_country") or ""),
        "数据接收国": str(route.get("recipient_country") or ""),
        "业务场景": str(route.get("scenario_text") or route.get("function") or ""),
    }

    catalog: list[dict[str, Any]] = [
        {
            "key": "company_name",
            "label": "公司名称",
            "question": "申报或评估主体企业全称",
            "current": str(route.get("company_name") or ""),
            "target": "route",
            "max": 120,
        },
        {
            "key": "vehicle_model",
            "label": "车型或车队",
            "question": "涉及的车型、车系或平台代号",
            "current": str(route.get("vehicle_model") or ""),
            "target": "route",
            "max": 120,
        },
    ]
    q_by_key: dict[str, dict[str, Any]] = {}
    for q in view.get("questions") or []:
        key = str(q.get("key") or "")
        if not key:
            continue
        q_by_key[key] = q
        cur_val = str(q.get("value") or "")
        if q.get("kind") == "risks" and cur_val:
            cur_shown = risk_items.shown(key, cur_val)
        else:
            cur_shown = cur_val
        entry: dict[str, Any] = {
            "key": key,
            "label": q.get("label", key),
            "question": q.get("question", ""),
            "hint": q.get("hint", ""),
            "current": cur_shown[:160],
            "target": "answer",
            "max": int(q.get("max") or 600),
        }
        if q.get("choices"):
            entry["choices"] = q["choices"]
        if q.get("detail_on"):
            entry["detail_on"] = q["detail_on"]
        if q.get("other"):
            entry["other"] = True
        if q.get("kind") == "risks":
            entry["kind"] = "risks"
        catalog.append(entry)

    recent = _trim_history(history, limit=4)
    batches = [catalog[i : i + DOC_BATCH] for i in range(0, len(catalog), DOC_BATCH)]
    results = await asyncio.gather(
        *(_ask_doc_batch(b, route_ctx, text, recent) for b in batches),
        return_exceptions=True,
    )
    raw_items = [item for r in results if not isinstance(r, Exception) for item in r if isinstance(item, dict)]

    meta_by_key = {f["key"]: f for f in catalog}
    msg_numbers = _allowed_numbers(text)
    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in raw_items:
        key = str(item.get("key") or "").strip()
        if not key or key in seen or key not in meta_by_key:
            continue
        meta = meta_by_key[key]
        quote = str(item.get("quote") or "").strip()
        if not _quote_matches(quote, text):
            continue

        q_orig = q_by_key.get(key)
        if meta.get("kind") == "risks" and isinstance(item.get("risk_row"), dict):
            rr = item["risk_row"]
            risk_text = str(rr.get("risk") or "").strip()
            if not risk_text or REMARK.search(risk_text):
                continue
            row_check_text = " ".join(str(rr.get(k) or "") for k in ("risk", "measures", "owner", "due"))
            if _decomma_numbers(row_check_text) - msg_numbers:
                continue
            prev_raw = str(q_orig.get("value") or "") if q_orig else ""
            existing_rows = risk_items.parse(prev_raw)
            new_row = {
                "id": "",
                "risk": risk_text,
                "likelihood": str(rr.get("likelihood") or "").strip(),
                "severity": str(rr.get("severity") or "").strip(),
                "measures": str(rr.get("measures") or "").strip(),
                "owner": str(rr.get("owner") or "").strip(),
                "due": str(rr.get("due") or "").strip(),
            }
            merged_rows = []
            replaced = False
            for ex in existing_rows:
                if not replaced and ex["risk"] and (ex["risk"] in risk_text or risk_text in ex["risk"]):
                    upd = {**ex, **{k: v for k, v in new_row.items() if v and k != "id"}}
                    merged_rows.append(upd)
                    replaced = True
                else:
                    merged_rows.append(ex)
            if not replaced:
                merged_rows.append(new_row)
            merged_val = risk_items.merge_history(prev_raw, risk_items.dump(merged_rows))
            if merged_val == prev_raw:
                continue
            parts = [f"风险：{risk_text}"]
            if new_row["likelihood"] in risk_items.LEVELS:
                parts.append(f"可能性：{new_row['likelihood']}")
            if new_row["severity"] in risk_items.LEVELS:
                parts.append(f"严重程度：{new_row['severity']}")
            if new_row["measures"]:
                parts.append(f"应对措施：{new_row['measures']}")
            if new_row["owner"]:
                parts.append(f"责任人：{new_row['owner']}")
            if new_row["due"]:
                parts.append(f"期限：{new_row['due']}")
            proposals.append(
                {
                    "key": key,
                    "label": meta["label"],
                    "value": merged_val,
                    "display_value": "；".join(parts),
                    "previous_value": prev_raw,
                    "previous_display": risk_items.shown(key, prev_raw)[:120] if prev_raw else "",
                    "note": f"依据原话：{quote[:60]}",
                    "quote": quote,
                    "target": "answer",
                }
            )
            seen.add(key)
            continue

        val = str(item.get("value") or "").strip()
        if not val or REMARK.search(val):
            continue
        if q_orig is not None:
            example = str(q_orig.get("example") or "").strip()
            if example and len(example) >= 4 and (val == example or example in val):
                if example not in text:
                    continue
            val = _format_doc_choice(q_orig, val)

        max_len = int(meta.get("max") or 600)
        if len(val) > max_len:
            continue

        choice_nums = _allowed_numbers(" ".join(meta.get("choices") or []))
        if _decomma_numbers(val) - (msg_numbers | choice_nums):
            continue

        prev_raw = (
            str(route.get(key) or "")
            if meta["target"] == "route"
            else str((q_orig or {}).get("value") or "")
        )
        if val == prev_raw.strip():
            continue

        proposals.append(
            {
                "key": key,
                "label": meta["label"],
                "value": val,
                "display_value": val,
                "previous_value": prev_raw,
                "previous_display": prev_raw[:120] if prev_raw.strip() else "",
                "note": f"依据原话：{quote[:60]}",
                "quote": quote,
                "target": meta["target"],
            }
        )
        seen.add(key)

    return proposals


def _display_form_value(item: dict[str, Any], val: Any) -> str:
    if val is None or val == "" or val == []:
        return ""
    labels = item.get("option_labels") or {}
    if item.get("kind") == "radio":
        k = str(val)
        zh = labels.get(k)
        return f"{zh}（{k}）" if zh and zh != k else k
    if item.get("kind") == "check" and isinstance(val, list):
        parts = []
        for k in val:
            ks = str(k)
            zh = labels.get(ks)
            parts.append(f"{zh}（{ks}）" if zh and zh != ks else ks)
        return "、".join(parts)
    return str(val)


async def _ask_form_extract_batch(
    form_meta: dict[str, Any],
    items_batch: list[dict[str, Any]],
    message: str,
    recent: list[dict[str, str]],
) -> list[dict[str, Any]]:
    payload = {
        "表格信息": form_meta,
        "最近对话": recent,
        "用户当前输入": message,
        "本批栏位列表": items_batch,
    }
    res = await call_llm_json(
        [
            {"role": "system", "content": FORM_EXTRACT_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=3600,
        temperature=0.1,
    )
    data = res.data if isinstance(res.data, dict) else {}
    got = data.get("proposals")
    return list(got) if isinstance(got, list) else []


async def _ask_form_reply(
    form_meta: dict[str, Any],
    section_overview: list[dict[str, Any]],
    message: str,
    recent: list[dict[str, str]],
) -> str:
    payload = {
        "表格信息": form_meta,
        "栏位概览": section_overview,
        "最近对话": recent,
        "用户当前输入": message,
    }
    res = await call_llm_json(
        [
            {"role": "system", "content": FORM_REPLY_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=700,
        temperature=0.2,
    )
    data = res.data if isinstance(res.data, dict) else {}
    return items_not_questions(str(data.get("reply") or "").strip())


async def assist_official_form(
    form_view: dict[str, Any],
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Conversational Q&A + foreign-language-aware proposal extraction for Forms.vue."""
    text = (message or "").strip()
    if PRIVACY_ASK.search(text):
        return {"reply": PRIVACY, "proposals": []}

    form_info = form_view.get("form") or {}
    lang_code = str(form_info.get("language") or "zh").lower()
    lang_label = LANG_NAME.get(lang_code, lang_code or "原表语种")
    form_meta = {
        "表格名称": form_info.get("name_zh", ""),
        "受理机构": form_info.get("authority_zh", ""),
        "国家": form_info.get("country", ""),
        "表格填写语种": f"{lang_label}（代码 {lang_code}）",
        "法律依据": form_info.get("legal_basis_zh", ""),
    }

    flat_items: list[dict[str, Any]] = []
    item_map: dict[str, dict[str, Any]] = {}
    section_overview: list[dict[str, Any]] = []

    for sec in form_view.get("sections") or []:
        sec_title = str(sec.get("title_zh") or sec.get("id") or "")
        sec_sample: list[str] = []
        for it in sec.get("items") or []:
            iid = str(it.get("id") or "")
            kind = str(it.get("kind") or "")
            sec_sample.append(f"{it.get('label_zh', iid)}（{kind}）")
            if kind not in ("text", "radio", "check") or not iid:
                continue
            item_map[iid] = {**it, "_section": sec_title}
            spec: dict[str, Any] = {
                "id": iid,
                "section": sec_title,
                "label_zh": it.get("label_zh", ""),
                "label_orig": it.get("label_fr", ""),
                "question_zh": it.get("question_zh", ""),
                "help_zh": it.get("help_zh", ""),
                "kind": kind,
            }
            if it.get("max_chars"):
                spec["max_chars"] = it["max_chars"]
            if it.get("options"):
                opts = it["options"]
                opt_labels = it.get("option_labels") or {}
                spec["options"] = {k: opt_labels.get(k, k) for k in opts}
            if it.get("value") not in (None, "", []):
                spec["current"] = it["value"]
            flat_items.append(spec)
        section_overview.append({"分节": sec_title, "包含栏位": sec_sample[:25]})

    recent = _trim_history(history, limit=6)
    skip_extract = is_pure_short_question(text)
    tasks: list[Any] = [_ask_form_reply(form_meta, section_overview, text, recent)]
    if not skip_extract and flat_items:
        for i in range(0, len(flat_items), FORM_BATCH):
            tasks.append(_ask_form_extract_batch(form_meta, flat_items[i : i + FORM_BATCH], text, recent))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    reply_raw = results[0] if results and not isinstance(results[0], Exception) else ""
    reply_text = str(reply_raw or "").strip()

    raw_proposals: list[dict[str, Any]] = []
    for r in results[1:]:
        if not isinstance(r, Exception) and isinstance(r, list):
            raw_proposals.extend(x for x in r if isinstance(x, dict))

    msg_numbers = _allowed_numbers(text)
    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()

    for p in raw_proposals:
        iid = str(p.get("key") or p.get("id") or "").strip()
        if not iid or iid in seen or iid not in item_map:
            continue
        it = item_map[iid]
        quote = str(p.get("quote") or "").strip()
        if not _quote_matches(quote, text):
            continue

        kind = it["kind"]
        raw_val = p.get("value")
        clean_val: str | list[str]
        if kind == "text":
            s = str(raw_val or "").strip()
            if not s or REMARK.search(s):
                continue
            limit = int(it.get("max_chars") or 400)
            if len(s) > limit:
                continue
            if _decomma_numbers(s) - msg_numbers:
                continue
            clean_val = s
        elif kind == "radio":
            s = str(raw_val or "").strip()
            opts = list(it.get("options") or [])
            if s not in opts:
                # 兼容模型返回了中文标签的情况
                opt_labels = it.get("option_labels") or {}
                matched = next((k for k, v in opt_labels.items() if v == s and k in opts), "")
                if not matched:
                    continue
                s = matched
            clean_val = s
        elif kind == "check":
            vals = raw_val if isinstance(raw_val, list) else ([raw_val] if raw_val else [])
            opts = list(it.get("options") or [])
            opt_labels = it.get("option_labels") or {}
            picked: list[str] = []
            for v in vals:
                sv = str(v or "").strip()
                if sv in opts and sv not in picked:
                    picked.append(sv)
                else:
                    matched = next((k for k, lbl in opt_labels.items() if lbl == sv and k in opts), "")
                    if matched and matched not in picked:
                        picked.append(matched)
            if not picked:
                continue
            clean_val = picked
        else:
            continue

        cur_val = it.get("value")
        if clean_val == cur_val:
            continue

        # 只有原本由本表用户填写（source=="answer"）的值撤回时写回旧值；带入值（assessment）或未填值撤回时传空以清空覆盖
        prev_save: str | list[str] = (
            cur_val
            if it.get("source") == "answer" and cur_val not in (None, "", [])
            else ([] if kind == "check" else "")
        )
        prev_disp = _display_form_value(it, cur_val)
        note_zh = str(p.get("note_zh") or "").strip()
        if note_zh and not note_zh.startswith("中文核对"):
            note_zh = f"中文核对：{note_zh}"
        note = note_zh or f"依据原话：{quote[:60]}"

        label = str(it.get("label_zh") or iid)
        if it.get("_section"):
            label = f"{it['_section']} · {label}"

        proposals.append(
            {
                "key": iid,
                "label": label,
                "value": clean_val,
                "display_value": _display_form_value(it, clean_val),
                "previous_value": prev_save,
                "previous_display": prev_disp[:120],
                "note": note,
                "quote": quote,
                "target": "form",
            }
        )
        seen.add(iid)

    if not reply_text or CLAIMED.search(reply_text):
        if proposals:
            reply_text = f"已从你的描述里整理出 {len(proposals)} 项对应栏位（按原表要求使用{lang_label}），核对下方卡片后点一键填入即可写入表格。"
        else:
            reply_text = f"这张表是{form_meta['受理机构']}的《{form_meta['表格名称']}》，要求用{lang_label}填写。你可以直接说出企业、联系人或数据处理情况，我会整理成对应栏位供你一键填入；也可以问任意一栏怎么填。"
    elif proposals and "一键填入" not in reply_text and "卡片" not in reply_text:
        reply_text += f" 我同时整理出了 {len(proposals)} 项可填内容，核对下方卡片后点一键填入即可写入。"

    return {"reply": reply_text, "proposals": proposals}


async def assist_admission_project(
    project_view: dict[str, Any],
    questions: list[dict[str, Any]],
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Conversational Q&A + verified proposal extraction for Admission.vue."""
    from app.admission.router import engine_check

    text = (message or "").strip()
    if PRIVACY_ASK.search(text):
        return {"reply": PRIVACY, "proposals": []}

    entry = project_view.get("entry") or {}
    overview_ctx = {
        "项目名称": project_view.get("title", ""),
        "受理机构": (entry.get("profile") or {}).get("authority_zh") or entry.get("authority", ""),
        "提交语种": project_view.get("foreign_lang_zh") or (entry.get("profile") or {}).get("language") or "",
        "功能场景": project_view.get("function_zh", ""),
        "材料递交清单": [
            {
                "材料": str(r[1]) if isinstance(r, (list, tuple)) and len(r) > 1 else (r.get("name", "") if isinstance(r, dict) else ""),
                "准备方式": str(r[2]) if isinstance(r, (list, tuple)) and len(r) > 2 else (r.get("source", "") if isinstance(r, dict) else ""),
            }
            for r in (project_view.get("checklist") or [])[:12]
        ],
    }

    field_list: list[dict[str, Any]] = []
    field_map: dict[str, dict[str, Any]] = {}

    for q in questions or []:
        k = str(q.get("key") or "")
        if not k:
            continue
        spec = {
            "key": k,
            "label": q.get("label", k),
            "question": q.get("question", ""),
            "why": q.get("why", ""),
            "example": q.get("example", ""),
            "current": "",
            "editable": False,
        }
        field_list.append(spec)
        field_map[k] = spec

    for f in project_view.get("filled") or []:
        k = str(f.get("key") or "")
        if not k or k in field_map:
            continue
        spec = {
            "key": k,
            "label": f.get("label", k),
            "question": f"已填项（当前值：{f.get('value', '')}）",
            "why": "",
            "example": "",
            "current": str(f.get("value") or ""),
            "editable": bool(f.get("editable")),
        }
        field_list.append(spec)
        field_map[k] = spec

    recent = _trim_history(history, limit=6)
    payload = {
        "准入项目概况": overview_ctx,
        "最近对话": recent,
        "用户当前输入": text,
        "栏位列表": field_list,
    }

    reply_text = ""
    raw_proposals: list[dict[str, Any]] = []
    try:
        res = await call_llm_json(
            [
                {"role": "system", "content": ADMISSION_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=3200,
            temperature=0.15,
        )
        data = res.data if isinstance(res.data, dict) else {}
        reply_text = items_not_questions(str(data.get("reply") or "").strip())
        if isinstance(data.get("proposals"), list) and not is_pure_short_question(text):
            raw_proposals = [x for x in data["proposals"] if isinstance(x, dict)]
    except Exception:  # noqa: BLE001
        reply_text = ""

    msg_numbers = _allowed_numbers(text)
    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()

    for p in raw_proposals:
        k = str(p.get("key") or "").strip()
        if not k or k in seen or k not in field_map:
            continue
        meta = field_map[k]
        val = str(p.get("value") or "").strip()
        quote = str(p.get("quote") or "").strip()
        if not val or len(val) > 500 or REMARK.search(val):
            continue
        if not _quote_matches(quote, text):
            continue
        if _decomma_numbers(val) - msg_numbers:
            continue
        ex = re.sub(r"^如：", "", str(meta.get("example") or "")).strip()
        if ex and len(ex) >= 4 and (val == ex or ex in val) and ex not in text:
            continue
        if engine_check(k, val):
            continue
        cur_val = str(meta.get("current") or "").strip()
        if val == cur_val:
            continue

        # 原本在本准入项目自填（editable=True）的撤回恢复旧值；从评估/他表带入或原本未填的撤回传空字符串清除覆盖
        prev_save = cur_val if meta.get("editable") else ""
        proposals.append(
            {
                "key": k,
                "label": meta["label"],
                "value": val,
                "display_value": val,
                "previous_value": prev_save,
                "previous_display": cur_val[:120],
                "note": f"依据原话：{quote[:60]}",
                "quote": quote,
                "target": "admission",
            }
        )
        seen.add(k)

    if not reply_text or CLAIMED.search(reply_text):
        if proposals:
            reply_text = f"已从你的描述里识别出 {len(proposals)} 项准入申报信息，核对下方卡片后点一键填入即可保存到本项目。"
        else:
            missing_labels = "、".join(q.get("label", "") for q in (questions or [])[:4] if q.get("label"))
            hint = f"目前还缺 {missing_labels} 等信息。" if missing_labels else "当前所需信息均已齐备，如需修改某栏可直接告诉我。"
            reply_text = f"这是《{overview_ctx['项目名称']}》的填报助手。{hint}你可以直接说出企业、联系人或登记编号，我会整理成卡片供你一键填入。"
    elif proposals and "一键填入" not in reply_text and "卡片" not in reply_text:
        reply_text += f" 我已同步整理出 {len(proposals)} 项可填信息，核对下方卡片后点一键填入即可写入。"

    return {"reply": reply_text, "proposals": proposals}

"""Command line for the five-country golden evaluation.

    uv run python -m app.analysis.golden_cli build
    uv run python -m app.analysis.golden_cli run --mode retrieval --out <dir>
    uv run python -m app.analysis.golden_cli run --mode full --out <dir> --concurrency 3

Secrets are read from the environment only (database URL, CYSH_LLM_API_KEY); nothing is
written to the outputs except questions, clause locators and scores. The run reads the
business database and never writes clauses; production model calls leave the usual
outbound-call audit rows because every model request is audited before it is sent.
"""

import argparse
import asyncio
import copy
import json
import sys
from datetime import datetime
from pathlib import Path


def pct(value):
    return "未测量" if value is None else f"{100 * value:.1f}%"


def ratio(num, den):
    return f"{num}/{den}"


def write_report(path, suite, mode, agg, gates, started):
    o = agg["overall"]
    mode_name = "检索 + 引用 + 事实一致性" if mode == "full" else "仅检索（不调用大模型）"
    topics = len({c["topic_id"] for c in suite["cases"]})
    mrr = "未测量" if o["mrr"] is None else f"{o['mrr']:.3f}"
    lines = [
        "# 五国黄金样本评测记录",
        "",
        f"运行时间：{started}　模式：{mode_name}",
        f"评测集：{len(suite['cases'])} 个案例（{topics} 个主题 × 3 种问法）　"
        f"哈希：`{suite['suite_sha256']}`",
        "",
        suite["note"],
        "",
        "## 指标与目标",
        "",
        "|指标|目标|结果|分子/分母|状态|",
        "|---|---:|---:|---:|---|",
        f"|检索召回率（前 5 条命中的必备条款组）|≥90%|{pct(gates['recall']['value'])}|"
        f"{ratio(o['groups_found'], o['groups_total'])}|{gates['recall']['status']}|",
        f"|条款定位准确率（引用条款落在期望条款内）|≥80%|{pct(gates['location']['value'])}|"
        f"{ratio(o['cited_correct'], o['findings_total'])}|{gates['location']['status']}|",
        f"|事实一致性（独立模型判定结论被引文完整支持）|≥90%|"
        f"{pct(gates['fact_consistency']['value'])}|"
        f"{ratio(o['supported_findings'], o['judged_findings'])}|"
        f"{gates['fact_consistency']['status']}|",
        "|按主题判相关：前 5 条至少有一条直接回答的案例（召回口径）|≥90%|"
        f"{pct(gates['recall_judged']['value'])}|"
        f"{o['judged_questions']} 个案例已判定|{gates['recall_judged']['status']}|",
        "|按主题判相关：引用条款被判直接回答（定位口径）|≥80%|"
        f"{pct(gates['location_judged']['value'])}|"
        f"|{gates['location_judged']['status']}|",
        "",
        "相关性判定为独立模型逐条判断，不依赖冻结条款列表，新来源进库后仍可比；"
        f"前 5 条平均相关占比 {pct(o['judged_precision_at_k'])}，"
        f"首条直接回答 {pct(o['judged_top1'])}。",
        "",
        f"辅助指标：首条命中率 {pct(o['hit_at_1'])}；平均倒数排名 {mrr}；"
        f"至少引用一条期望条款的案例 {pct(o['question_location_hit'])}"
        f"（{o['answered_questions']} 个案例已生成结论）；"
        f"判定覆盖率 {pct(o['judge_coverage'])}；技术错误 {o['technical_error_questions']} 个案例；"
        f"来源漂移 {o['drift_questions']} 个案例。",
        "",
        "## 按国家",
        "",
        "|国家|案例数|召回|首条命中|定位|事实一致性|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for code, b in agg["by_country"].items():
        recall = f"{pct(b['recall_at_k'])} ({ratio(b['groups_found'], b['groups_total'])})"
        lines.append(
            f"|{code}|{b['questions']}|{recall}"
            f"|{pct(b['hit_at_1'])}|{pct(b['location_accuracy'])}|{pct(b['fact_consistency'])}|"
        )
    lines += [
        "",
        "## 按问法",
        "",
        "|问法|案例数|召回|首条命中|定位|事实一致性|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    names = {"keyword": "关键词", "scenario": "业务场景", "datatype": "数据类型"}
    for style, b in agg["by_style"].items():
        lines.append(
            f"|{names.get(style, style)}|{b['questions']}|{pct(b['recall_at_k'])}"
            f"|{pct(b['hit_at_1'])}|{pct(b['location_accuracy'])}|{pct(b['fact_consistency'])}|"
        )
    lines += ["", "## 按分析维度", "", "|维度|题数|召回|首条命中|", "|---|---:|---:|---:|"]
    for dim, b in agg["by_dimension"].items():
        lines.append(f"|{dim}|{b['questions']}|{pct(b['recall_at_k'])}|{pct(b['hit_at_1'])}|")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def build(args):
    from app.analysis import golden_eval as ge
    from app.analysis.golden_spec import PARALLEL_COPIES, TOPICS
    from app.analysis.tools import ToolBox, is_usable_clause_text
    from app.core.db import get_sessionmaker

    pools = {}
    async with get_sessionmaker()() as session:
        for code in sorted({t["country_code"] for t in TOPICS}):
            candidates = await ToolBox(session, code)._load_candidates()
            pools[code] = [c for c in candidates if is_usable_clause_text(c.text)]
    suite = ge.build_suite(TOPICS, pools, PARALLEL_COPIES)
    Path(args.suite).write_text(
        json.dumps(suite, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"已冻结 {len(suite['cases'])} 个案例 -> {args.suite}")


async def corpus_fingerprints(sf=None) -> dict[str, str]:
    """每个国家当前法规数据的指纹：范围内文档的最新已解析版本，加上通过核验的现行译文条数。
    文本或译文变了指纹就变，评测结果才不会拿旧数据当新数据。"""
    import hashlib

    from sqlalchemy import text

    from app.core import egress_models  # noqa: F401
    from app.core.db import get_sessionmaker
    from app.core.rls import system_scope

    with system_scope("golden-fingerprint"):
        async with (sf or get_sessionmaker())() as session:
            versions = (
                await session.execute(
                    text(
                        """select d.country_code, v.id from documents d
                        join document_versions v on v.document_id=d.id and v.state='parsed'
                        and v.version_no=(select max(version_no) from document_versions x
                                          where x.document_id=d.id and x.state='parsed')
                        where d.relevance not in ('out_of_scope','irrelevant')"""
                    )
                )
            ).all()
            translated = dict(
                (
                    await session.execute(
                        text(
                            """select d.country_code, count(*) from clause_translations t
                            join clauses k on k.id=t.clause_id
                            join document_versions v on v.id=k.version_id
                            join documents d on d.id=v.document_id
                            where t.is_current and t.quality_check_passed group by 1"""
                        )
                    )
                ).all()
            )
    parts: dict[str, list[str]] = {}
    for country, vid in versions:
        parts.setdefault(country, []).append(str(vid))
    return {
        c: hashlib.sha256(
            ("|".join(sorted(ids)) + f"#{translated.get(c, 0)}").encode()
        ).hexdigest()[:16]
        for c, ids in parts.items()
    }


async def run(args):
    from app.analysis import golden_eval as ge
    from app.core.db import get_sessionmaker

    suite = ge.load_suite(args.suite)
    cases = suite["cases"]
    if args.country:
        cases = [c for c in cases if c["country_code"] in args.country]
    if args.limit:
        cases = cases[: args.limit]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = out / (args.resume_name or f"golden_{args.mode}_{stamp}.jsonl")
    fingerprints = await corpus_fingerprints()
    case_country = {c["id"]: c["country_code"] for c in cases}
    done = {}
    stale = 0
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("suite_sha256") != suite["suite_sha256"]:
                continue
            # 只有这个国家的法规数据没变，旧结果才可复用；没有指纹的旧结果按“变了”处理
            if row.get("corpus_fp") != fingerprints.get(case_country.get(row["question_id"])):
                stale += 1
                continue
            done[row["question_id"]] = row
    if stale:
        print(f"数据有变动：{stale} 个案例重新评测，其余 {len(done)} 个沿用旧结果", flush=True)

    retrieval_config = analysis_config = None
    if args.mode == "full" or args.planning:
        from app.analysis.workflow_engine import ensure_workflow_config
        from app.analysis.workflow_runtime import execution_settings, require_executable

        async with get_sessionmaker()() as session:
            cfg = await ensure_workflow_config(session)
            require_executable(cfg)
            nodes = copy.deepcopy(cfg.nodes_json)
        if args.baseline:
            # 提示词取代码里的最新基线，其余参数沿用已激活的版本：只读，不改库里的激活版本，
            # 前后两次评测只差提示词这一项。
            from app.analysis.workflow_runtime import baseline_nodes

            fresh = {n["node_id"]: n for n in baseline_nodes()}
            for node in nodes:
                if node["node_id"] in ("node_dimension_analyst", "node_mcp_retriever"):
                    node["system_prompt"] = fresh[node["node_id"]]["system_prompt"]
        analysis_config = execution_settings(
            next(n for n in nodes if n["node_id"] == "node_dimension_analyst")
        )
        retrieval_config = execution_settings(
            next(n for n in nodes if n["node_id"] == "node_mcp_retriever")
        )
        if args.mode == "retrieval" and not args.planning:
            retrieval_config = None

    todo = [c for c in cases if c["id"] not in done]
    sem = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()
    counter = {"n": len(done)}

    async def work(case):
        async with sem:
            async with get_sessionmaker()() as session:
                row = await ge.run_case(
                    session,
                    case,
                    mode=args.mode,
                    retrieval_config=retrieval_config,
                    analysis_config=analysis_config,
                )
        row["suite_sha256"] = suite["suite_sha256"]
        row["corpus_fp"] = fingerprints.get(case["country_code"])
        async with lock:
            with results_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            done[row["question_id"]] = row
            counter["n"] += 1
            print(f"[{counter['n']}/{len(cases)}] {row['question_id']}", flush=True)

    await asyncio.gather(*(work(c) for c in todo))
    rows = [done[c["id"]] for c in cases if c["id"] in done]
    agg = ge.aggregate(rows)
    gates = ge.evaluate_gates(agg["overall"])
    summary = {
        "suite_sha256": suite["suite_sha256"],
        "mode": args.mode,
        "aggregate": agg,
        "gates": gates,
        "results_file": results_path.name,
    }
    (out / (results_path.stem + "_summary.json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    report = out / (results_path.stem + "_report.md")
    write_report(report, {**suite, "cases": cases}, args.mode, agg, gates, stamp)
    print(f"报告：{report}")
    for name, g in gates.items():
        print(name, g["status"], pct(g["value"]))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    default_suite = str(Path(__file__).with_name("golden_cases.json"))
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="resolve spec locators against the live corpus and freeze")
    b.add_argument("--suite", default=default_suite)
    r = sub.add_parser("run", help="run the frozen suite")
    r.add_argument("--suite", default=default_suite)
    r.add_argument("--mode", choices=("retrieval", "full"), default="retrieval")
    r.add_argument("--planning", action="store_true", help="use production semantic planning")
    r.add_argument(
        "--baseline",
        action="store_true",
        help="latest code prompts on top of the active workflow settings",
    )
    r.add_argument("--out", required=True)
    r.add_argument("--country", action="append")
    r.add_argument("--limit", type=int)
    r.add_argument("--concurrency", type=int, default=2)
    r.add_argument("--resume-name")
    args = parser.parse_args()
    asyncio.run(build(args) if args.command == "build" else run(args))


if __name__ == "__main__":
    main()

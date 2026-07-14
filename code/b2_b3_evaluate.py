from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

from common.path_utils import bootstrap_project_root


bootstrap_project_root()

from b3.context import ToolExecutionContext
from b3_tool_layer import execute_tool_calls, get_tools_schema
from common.io_utils import read_json, read_yaml, write_json
from common.path_utils import resolve_cli_path, resolve_from_file
from skills.errors import SkillError
from skills.local_file_search import local_file_search
import skills.web_fetcher as web_fetcher_module


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record must be an object at {path}:{line_number}")
        records.append(record)
    return records


def _decode_message(message: dict[str, Any]) -> dict[str, Any]:
    return json.loads(message["content"])


def _retrieval_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["split"]].append(record)

    def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(items)
        reciprocal_ranks = [1.0 / item["rank"] if item["rank"] else 0.0 for item in items]
        latencies = sorted(item["latency_ms"] for item in items)

        def percentile(fraction: float) -> float:
            if not latencies:
                return 0.0
            index = min(len(latencies) - 1, max(0, int(len(latencies) * fraction + 0.999999) - 1))
            return round(latencies[index], 3)

        return {
            "query_count": total,
            **{
                f"recall_at_{cutoff}": round(
                    sum(item["rank"] is not None and item["rank"] <= cutoff for item in items)
                    / total,
                    6,
                )
                if total
                else 0.0
                for cutoff in (1, 3, 5)
            },
            "mrr": round(sum(reciprocal_ranks) / total, 6) if total else 0.0,
            "empty_result_rate": round(
                sum(item["result_count"] == 0 for item in items) / total, 6
            )
            if total
            else 0.0,
            "mean_latency_ms": round(
                sum(item["latency_ms"] for item in items) / total, 3
            )
            if total
            else 0.0,
            "first_query_latency_ms": round(items[0]["latency_ms"], 3) if items else 0.0,
            "warm_mean_latency_ms": round(
                sum(item["latency_ms"] for item in items[1:]) / (total - 1), 3
            )
            if total > 1
            else 0.0,
            "p50_latency_ms": percentile(0.50),
            "p95_latency_ms": percentile(0.95),
        }

    return {
        "overall": aggregate(records),
        "by_split": {split: aggregate(items) for split, items in sorted(grouped.items())},
    }


def evaluate_retrieval(
    dataset_root: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    corpus = _read_jsonl(dataset_root / "retrieval" / "corpus.jsonl")
    queries = _read_jsonl(dataset_root / "retrieval" / "queries.jsonl")
    path_to_doc = {record["path"]: record["doc_id"] for record in corpus}
    config = read_yaml(config_path)
    settings = config["settings"]
    data_root = resolve_from_file(settings["data_root"], config_path)
    model_path = resolve_from_file(settings["semantic_search"]["model_path"], config_path)
    variants: dict[str, Any] = {}
    for mode in ("keyword", "semantic", "hybrid"):
        variant_dir = output_dir / mode
        if mode != "keyword" and not model_path.is_dir():
            variants[mode] = {
                "status": "unverified",
                "reason": f"local embedding model directory is absent: {model_path}",
            }
            write_json(variants[mode], variant_dir / "retrieval_report.json")
            continue
        query_records = []
        try:
            for query in queries:
                started = perf_counter()
                result = local_file_search(
                    query["query"],
                    root_dir="benchmarks/b2_b3_v1/retrieval/corpus",
                    file_types=["md"],
                    top_k=5,
                    mode=mode,
                    data_root=str(data_root),
                    embedding_model_path=str(model_path),
                )
                latency_ms = round((perf_counter() - started) * 1000, 3)
                returned_ids = [
                    path_to_doc[item["path"]]
                    for item in result["results"]
                    if item["path"] in path_to_doc
                ]
                relevant = set(query["relevant_doc_ids"])
                rank = next(
                    (index for index, doc_id in enumerate(returned_ids, 1) if doc_id in relevant),
                    None,
                )
                query_records.append(
                    {
                        "query_id": query["query_id"],
                        "split": query["split"],
                        "relevant_doc_ids": query["relevant_doc_ids"],
                        "returned_doc_ids": returned_ids,
                        "rank": rank,
                        "result_count": len(returned_ids),
                        "latency_ms": latency_ms,
                    }
                )
        except SkillError as exc:
            variants[mode] = {
                "status": "failed",
                "error": exc.to_dict(),
            }
            write_json(variants[mode], variant_dir / "retrieval_report.json")
            continue
        report = {
            "status": "success",
            "mode": mode,
            "metrics": _retrieval_metrics(query_records),
            "queries": query_records,
        }
        variants[mode] = report
        write_json(report, variant_dir / "retrieval_report.json")
    return variants


def _retrieval_comparison(variants: dict[str, Any]) -> dict[str, Any]:
    baseline = variants.get("keyword", {})
    if baseline.get("status") != "success":
        return {"status": "unavailable", "reason": "keyword baseline did not complete"}
    comparison: dict[str, Any] = {"status": "success", "baseline": "keyword", "variants": {}}
    baseline_metrics = baseline["metrics"]
    for mode in ("semantic", "hybrid"):
        result = variants.get(mode, {})
        if result.get("status") != "success":
            comparison["variants"][mode] = {
                "status": result.get("status", "unavailable"),
                "reason": result.get("reason") or result.get("error"),
            }
            continue
        deltas: dict[str, Any] = {}
        for group in ("overall", *sorted(baseline_metrics["by_split"])):
            baseline_group = (
                baseline_metrics["overall"]
                if group == "overall"
                else baseline_metrics["by_split"][group]
            )
            enhanced_group = (
                result["metrics"]["overall"]
                if group == "overall"
                else result["metrics"]["by_split"][group]
            )
            deltas[group] = {
                metric: round(enhanced_group[metric] - baseline_group[metric], 6)
                for metric in ("recall_at_1", "recall_at_3", "recall_at_5", "mrr")
            }
        comparison["variants"][mode] = {"status": "success", "delta_vs_keyword": deltas}
    return comparison


def evaluate_schema(config_path: Path, output_dir: Path) -> dict[str, Any]:
    variants = {}
    for mode in ("manual", "auto"):
        variant_dir = output_dir / mode
        started = perf_counter()
        schemas = get_tools_schema(
            str(config_path), "all_tools", str(variant_dir), schema_mode=mode
        )
        latency_ms = round((perf_counter() - started) * 1000, 3)
        diff = read_json(variant_dir / "schema_diff_report.json")
        variants[mode] = {
            "status": "success",
            "tool_count": len(schemas),
            "generation_latency_ms": latency_ms,
            "structural_difference_count": diff["structural_difference_count"],
            "compatible_tool_count": diff["compatible_tool_count"],
        }
    return variants


def evaluate_cache(config_path: Path, output_dir: Path) -> dict[str, Any]:
    calls = [
        {
            "id": f"cache-{index:03d}",
            "name": "calculator",
            "args": {"expression": "144 / 12"},
        }
        for index in range(20)
    ]
    variants = {}
    for enabled in (False, True):
        name = "on" if enabled else "off"
        context = ToolExecutionContext(f"cache-{name}")
        started = perf_counter()
        messages = execute_tool_calls(
            calls,
            str(config_path),
            "basic_tools",
            str(output_dir / name),
            execution_context=context,
            enable_cache=enabled,
        )
        wall_latency_ms = round((perf_counter() - started) * 1000, 3)
        metrics = context.metrics.snapshot()
        variants[name] = {
            "status": "success"
            if all(message["status"] == "success" for message in messages)
            else "failed",
            "wall_latency_ms": wall_latency_ms,
            "cache_hits": metrics["cache_hits"],
            "cache_hit_rate": metrics["cache_hit_rate"],
            "actual_execution_count": len(calls) - metrics["cache_hits"],
            "latency_ms": metrics["latency_ms"],
        }
    return variants


def evaluate_retry(config_path: Path, output_dir: Path) -> dict[str, Any]:
    variants = {}
    for enabled in (False, True):
        name = "on" if enabled else "off"
        attempt_counter = {"count": 0}

        def injected_web_fetcher(url: str) -> dict[str, Any]:
            attempt_counter["count"] += 1
            if attempt_counter["count"] == 1:
                raise SkillError(
                    "TRANSIENT_NETWORK_ERROR",
                    "evaluation fault injection",
                    category="network",
                    retryable=True,
                )
            return {"url": url, "text": "recovered by bounded retry"}

        injected_web_fetcher.__name__ = "web_fetcher"
        variant_dir = output_dir / name
        with patch.object(web_fetcher_module, "web_fetcher", injected_web_fetcher):
            message = execute_tool_calls(
                [
                    {
                        "id": f"retry-{name}",
                        "name": "web_fetcher",
                        "args": {"url": "https://example.com"},
                    }
                ],
                str(config_path),
                "network_tools",
                str(variant_dir),
                enable_retry=enabled,
                max_attempts=2,
            )[0]
        log_line = (variant_dir / "tool_call_log.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        log = json.loads(log_line)
        variants[name] = {
            "status": _decode_message(message)["status"],
            "attempts": attempt_counter["count"],
            "retry_count": log["retry_count"],
            "method": "recoverable fault injection; no mock success payload outside retry",
        }
    return variants


def evaluate_execution_cases(
    dataset_root: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cases = _read_jsonl(dataset_root / "tool_execution" / "cases.jsonl")
    records = []
    skipped_scenarios = {"cache", "cache_guard", "retry", "retry_ablation", "retry_guard"}
    for case in cases:
        if case["scenario"] in skipped_scenarios or any(
            call.get("args", {}).get("mode") in {"semantic", "hybrid"}
            for call in case["tool_calls"]
        ):
            records.append(
                {
                    "case_id": case["case_id"],
                    "status": "skipped",
                    "reason": "covered by a dedicated ablation or unavailable semantic model",
                }
            )
            continue
        messages = execute_tool_calls(
            case["tool_calls"],
            str(config_path),
            "all_tools",
            str(output_dir / case["case_id"]),
        )
        actual_statuses = [message["status"] for message in messages]
        actual_codes = [
            (_decode_message(message).get("error") or {}).get("code") for message in messages
        ]
        status_match = actual_statuses == case["expected_statuses"]
        expected_codes = case.get("expected_error_codes")
        code_match = expected_codes is None or actual_codes == expected_codes
        records.append(
            {
                "case_id": case["case_id"],
                "status": "passed" if status_match and code_match else "failed",
                "actual_statuses": actual_statuses,
                "expected_statuses": case["expected_statuses"],
                "actual_error_codes": actual_codes,
                "expected_error_codes": expected_codes,
            }
        )
    evaluated = [record for record in records if record["status"] != "skipped"]
    report = {
        "status": "success" if all(record["status"] == "passed" for record in evaluated) else "failed",
        "total_cases": len(records),
        "evaluated_cases": len(evaluated),
        "passed_cases": sum(record["status"] == "passed" for record in evaluated),
        "skipped_cases": sum(record["status"] == "skipped" for record in records),
        "records": records,
    }
    write_json(report, output_dir / "tool_execution_report.json")
    return report


def evaluate_safety(
    dataset_root: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cases = _read_jsonl(dataset_root / "safety" / "cases.jsonl")
    records = []
    toolset_by_tool = {
        "file_reader": "all_tools",
        "format_converter": "all_tools",
        "list_directory": "all_tools",
        "python_executor": "sandbox_tools",
        "web_fetcher": "network_tools",
    }
    for case in cases:
        if case["tool"] == "web_fetcher" and (
            case["expected_decision"] == "allow" or "redirect_chain" in case.get("policy_fixture", {})
        ):
            records.append(
                {
                    "case_id": case["case_id"],
                    "status": "skipped",
                    "reason": "requires external HTTP fixture; local policy checks are evaluated separately",
                }
            )
            continue
        message = execute_tool_calls(
            [{"id": case["case_id"], "name": case["tool"], "args": case["args"]}],
            str(config_path),
            toolset_by_tool[case["tool"]],
            str(output_dir / case["case_id"]),
        )[0]
        result = _decode_message(message)
        actual_decision = "allow" if result["status"] == "success" else "reject"
        actual_code = (result.get("error") or {}).get("code")
        passed = actual_decision == case["expected_decision"] and (
            not case.get("expected_error_code") or actual_code == case["expected_error_code"]
        )
        records.append(
            {
                "case_id": case["case_id"],
                "status": "passed" if passed else "failed",
                "expected_decision": case["expected_decision"],
                "actual_decision": actual_decision,
                "expected_error_code": case.get("expected_error_code"),
                "actual_error_code": actual_code,
            }
        )
    evaluated = [record for record in records if record["status"] != "skipped"]
    report = {
        "status": "success" if all(record["status"] == "passed" for record in evaluated) else "failed",
        "total_cases": len(records),
        "evaluated_cases": len(evaluated),
        "passed_cases": sum(record["status"] == "passed" for record in evaluated),
        "skipped_cases": sum(record["status"] == "skipped" for record in records),
        "records": records,
    }
    write_json(report, output_dir / "safety_report.json")
    return report


def _write_summary_csv(report: dict[str, Any], path: Path) -> None:
    rows: list[dict[str, Any]] = []
    for mode, result in report["retrieval"].items():
        if result["status"] == "success":
            metrics = result["metrics"]["overall"]
            rows.extend(
                {"experiment": "retrieval", "variant": mode, "status": "success", "metric": metric, "value": metrics[metric], "notes": "36-query benchmark"}
                for metric in (
                    "recall_at_1",
                    "recall_at_3",
                    "recall_at_5",
                    "mrr",
                    "empty_result_rate",
                    "mean_latency_ms",
                    "first_query_latency_ms",
                    "warm_mean_latency_ms",
                    "p50_latency_ms",
                    "p95_latency_ms",
                )
            )
        else:
            rows.append(
                {"experiment": "retrieval", "variant": mode, "status": result["status"], "metric": "not_available", "value": "", "notes": result.get("reason", "")}
            )
    for mode, result in report["schema"].items():
        rows.append(
            {"experiment": "schema", "variant": mode, "status": result["status"], "metric": "generation_latency_ms", "value": result["generation_latency_ms"], "notes": f"structural_diff={result['structural_difference_count']}"}
        )
    for mode, result in report["cache"].items():
        rows.append(
            {"experiment": "cache", "variant": mode, "status": result["status"], "metric": "actual_execution_count", "value": result["actual_execution_count"], "notes": f"hit_rate={result['cache_hit_rate']}"}
        )
    for mode, result in report["retry"].items():
        rows.append(
            {"experiment": "retry", "variant": mode, "status": result["status"], "metric": "attempts", "value": result["attempts"], "notes": "recoverable fault injection"}
        )
    for name in ("tool_execution", "safety"):
        result = report[name]
        rows.append(
            {"experiment": name, "variant": "enhanced", "status": result["status"], "metric": "passed_cases", "value": result["passed_cases"], "notes": f"evaluated={result['evaluated_cases']}; skipped={result['skipped_cases']}"}
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("experiment", "variant", "status", "metric", "value", "notes"),
        )
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the B2/B3 benchmark and ablations.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--tools_config", required=True)
    parser.add_argument("--outdir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset_root = resolve_cli_path(args.dataset)
    config_path = resolve_cli_path(args.tools_config)
    output_dir = resolve_cli_path(args.outdir)
    if not (dataset_root / "manifest.json").is_file():
        raise ValueError("dataset must contain manifest.json")
    retrieval = evaluate_retrieval(dataset_root, config_path, output_dir / "retrieval")
    report = {
        "status": "success",
        "environment": {
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "dataset": read_json(dataset_root / "manifest.json"),
        "retrieval": retrieval,
        "retrieval_comparison": _retrieval_comparison(retrieval),
        "schema": evaluate_schema(config_path, output_dir / "schema"),
        "cache": evaluate_cache(config_path, output_dir / "cache"),
        "retry": evaluate_retry(config_path, output_dir / "retry"),
        "tool_execution": evaluate_execution_cases(
            dataset_root, config_path, output_dir / "tool_execution"
        ),
        "safety": evaluate_safety(dataset_root, config_path, output_dir / "safety"),
    }
    if any(
        section.get("status") == "failed"
        for section in (report["tool_execution"], report["safety"])
    ):
        report["status"] = "failed"
    write_json(report, output_dir / "ablation_report.json")
    _write_summary_csv(report, output_dir / "ablation_summary.csv")
    print(output_dir / "ablation_report.json")
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

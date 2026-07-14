from __future__ import annotations

import argparse
import importlib
import inspect
import sys
from pathlib import Path
from time import perf_counter

from common.io_utils import append_jsonl, read_json, read_yaml, write_json
from common.logging_utils import now_iso
from common.path_utils import (
    DEFAULT_DATA_ROOT,
    bootstrap_project_root,
    resolve_cli_path,
    resolve_from_file,
)
from common.schemas import make_skill_result


bootstrap_project_root()

from skills.errors import SkillError, exception_to_error


SKILL_MODULES = {
    "calculator": "skills.calculator",
    "file_reader": "skills.file_reader",
    "local_file_search": "skills.local_file_search",
    "table_analyzer": "skills.table_analyzer",
    "format_converter": "skills.format_converter",
    "list_directory": "skills.list_directory",
    "python_executor": "skills.python_executor",
    "web_fetcher": "skills.web_fetcher",
    "read_and_convert": "skills.read_and_convert",
}


def _load_injected_settings(
    tools_config: str | None,
    function: object,
) -> tuple[dict, str | None, dict | None]:
    if not tools_config:
        return {}, None, None
    config_path = Path(tools_config).resolve()
    config = read_yaml(config_path)
    if not isinstance(config, dict) or not isinstance(config.get("settings"), dict):
        raise ValueError("tools config must contain settings")
    settings = config["settings"]
    signature = inspect.signature(function)
    injections: dict = {}

    def inject(name: str, value: object) -> None:
        if name in signature.parameters and value is not None:
            injections[name] = value

    limits = settings.get("limits", {})
    semantic = settings.get("semantic_search", {})
    python_settings = settings.get("python_executor", {})
    web_settings = settings.get("web_fetcher", {})
    inject("max_file_bytes", limits.get("max_file_bytes"))
    inject("max_files", limits.get("max_search_files"))
    inject("max_rows_limit", limits.get("max_table_rows"))
    model_path = semantic.get("model_path")
    if isinstance(model_path, str):
        inject("embedding_model_path", str(resolve_from_file(model_path, config_path)))
    for name in ("max_output_chars", "memory_limit_mb"):
        inject(name, python_settings.get(name))
    for name in (
        "allowed_domains",
        "allow_http",
        "max_response_bytes",
        "allowed_content_types",
        "max_redirects",
    ):
        inject(name, web_settings.get(name))
    data_root_setting = settings.get("data_root")
    resolved_data_root = (
        str(resolve_from_file(data_root_setting, config_path))
        if isinstance(data_root_setting, str)
        else None
    )
    return injections, resolved_data_root, config


def _enforce_feature_switches(
    skill_name: str,
    input_data: dict,
    config: dict | None,
) -> None:
    if config is None:
        return
    features = config["settings"].get("features", {})
    if not isinstance(features, dict):
        raise ValueError("settings.features must be an object")
    definition = config.get("tools", {}).get(skill_name, {})
    if definition.get("enabled", True) is not True:
        raise SkillError(
            "TOOL_DISABLED",
            f"tool is disabled by configuration: {skill_name}",
            category="configuration",
        )
    feature = definition.get("feature")
    if feature and features.get(feature, True) is not True:
        raise SkillError(
            "FEATURE_DISABLED",
            f"feature is disabled by configuration: {feature}",
            category="configuration",
        )
    if skill_name == "local_file_search" and input_data.get("mode", "keyword") in {
        "semantic",
        "hybrid",
    }:
        if features.get("semantic_search", True) is not True:
            raise SkillError(
                "FEATURE_DISABLED",
                "semantic search is disabled by configuration",
                category="configuration",
            )


def run_skill(
    skill_name: str,
    input_data: dict,
    data_root: str | None = None,
    output_dir: str | None = None,
    tools_config: str | None = None,
) -> dict:
    """Execute one registered Skill and return the stable SkillResult mapping.

    Args:
        skill_name: Registered Skill name.
        input_data: Keyword arguments passed to the Skill.
        data_root: Optional readable data root injected into compatible Skills.
        output_dir: Optional artifact directory injected into compatible Skills.
        tools_config: Optional policy configuration used for framework injections.

    Returns:
        A backward-compatible SkillResult with an extended error payload on failure.

    Raises:
        ValueError: If the Skill name or top-level input object is invalid.
    """
    if skill_name not in SKILL_MODULES:
        raise ValueError(f"unknown skill: {skill_name}")
    if not isinstance(input_data, dict):
        raise ValueError("skill input must be a JSON object")
    module = importlib.import_module(SKILL_MODULES[skill_name])
    function = getattr(module, skill_name)
    kwargs = dict(input_data)
    signature = inspect.signature(function)
    start = perf_counter()
    try:
        injected, configured_data_root, config = _load_injected_settings(tools_config, function)
        _enforce_feature_switches(skill_name, input_data, config)
        kwargs.update(injected)
        if "data_root" in signature.parameters:
            kwargs["data_root"] = data_root or configured_data_root or str(DEFAULT_DATA_ROOT)
        if "output_dir" in signature.parameters:
            kwargs["output_dir"] = output_dir
        output = function(**kwargs)
        status = "success"
        error = None
    except Exception as exc:  # Skill exceptions are a structured business result.
        output = None
        status = "error"
        error = exception_to_error(exc)
    latency_ms = round((perf_counter() - start) * 1000, 3)
    return make_skill_result(skill_name, status, input_data, output, error, latency_ms)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one local Agent skill.")
    parser.add_argument("--skill", required=True, choices=sorted(SKILL_MODULES))
    parser.add_argument("--input", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--data_root", default=None)
    parser.add_argument("--tools_config", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        input_path = resolve_cli_path(args.input)
        outdir = resolve_cli_path(args.outdir)
        input_data = read_json(input_path)
        data_root = str(resolve_cli_path(args.data_root)) if args.data_root else None
        outdir.mkdir(parents=True, exist_ok=True)
        tools_config = str(resolve_cli_path(args.tools_config)) if args.tools_config else None
        result = run_skill(args.skill, input_data, data_root, str(outdir), tools_config)
        result_path = outdir / f"{args.skill}_result.json"
        write_json(result, result_path)
        append_jsonl(
            {
                "timestamp": now_iso(),
                "skill_name": args.skill,
                "status": result["status"],
                "result_path": str(result_path),
                "latency_ms": result["latency_ms"],
            },
            outdir / "skill_run_log.jsonl",
        )
        print(result_path)
        return 0
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

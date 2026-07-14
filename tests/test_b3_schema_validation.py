from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
for entry in (str(PROJECT_ROOT), str(CODE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from b3_tool_layer import execute_tool_calls, get_tools_schema


CONFIG_PATH = PROJECT_ROOT / "configs" / "tools.yaml"


class B3SchemaValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary.name) / "outputs"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def decode(message: dict[str, Any]) -> dict[str, Any]:
        return json.loads(message["content"])

    def write_config(self, mutate: Any) -> Path:
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        mutate(config)
        target = Path(self.temporary.name) / "tools.yaml"
        target.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return target

    def test_public_function_positional_contract_is_preserved(self) -> None:
        schema_parameters = list(inspect.signature(get_tools_schema).parameters.values())
        execute_parameters = list(inspect.signature(execute_tool_calls).parameters.values())
        self.assertEqual(
            [parameter.name for parameter in schema_parameters[:3]],
            ["tools_config", "toolset", "outdir"],
        )
        self.assertEqual(
            [parameter.name for parameter in execute_parameters[:4]],
            ["tool_calls", "tools_config", "toolset", "outdir"],
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in execute_parameters[4:]
            )
        )

    def test_manual_and_auto_schema_are_structurally_compatible(self) -> None:
        manual = get_tools_schema(
            str(CONFIG_PATH), "all_tools", str(self.output_dir / "manual"), schema_mode="manual"
        )
        automatic = get_tools_schema(
            str(CONFIG_PATH), "all_tools", str(self.output_dir / "auto"), schema_mode="auto"
        )
        self.assertEqual([item["function"]["name"] for item in manual], [item["function"]["name"] for item in automatic])
        report = json.loads(
            (self.output_dir / "auto" / "schema_diff_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["structural_difference_count"], 0)
        self.assertEqual(report["compatible_tool_count"], len(automatic))

    def test_auto_schema_hides_framework_injected_parameters(self) -> None:
        schemas = get_tools_schema(str(CONFIG_PATH), "all_tools", schema_mode="auto")
        by_name = {item["function"]["name"]: item["function"] for item in schemas}
        self.assertNotIn("data_root", by_name["file_reader"]["parameters"]["properties"])
        self.assertNotIn("max_file_bytes", by_name["file_reader"]["parameters"]["properties"])
        self.assertNotIn("embedding_model_path", by_name["local_file_search"]["parameters"]["properties"])
        self.assertNotIn("output_dir", by_name["format_converter"]["parameters"]["properties"])
        self.assertNotIn("allowed_domains", by_name["web_fetcher"]["parameters"]["properties"])
        self.assertNotIn("memory_limit_mb", by_name["python_executor"]["parameters"]["properties"])
        self.assertEqual(
            by_name["format_converter"]["parameters"]["properties"]["target_format"]["enum"],
            ["markdown", "json"],
        )

    def test_auto_registration_fails_for_missing_function(self) -> None:
        config_path = self.write_config(
            lambda config: config["tools"]["calculator"].update(function="missing_function")
        )
        with self.assertRaisesRegex(ValueError, "registration failed"):
            get_tools_schema(str(config_path), "basic_tools", schema_mode="auto")

    def test_auto_schema_feature_switch_is_enforced(self) -> None:
        config_path = self.write_config(
            lambda config: config["settings"]["features"].update(auto_schema=False)
        )
        with self.assertRaisesRegex(ValueError, "disabled"):
            get_tools_schema(str(config_path), "basic_tools", schema_mode="auto")
        manual = get_tools_schema(str(config_path), "basic_tools", schema_mode="manual")
        self.assertEqual(len(manual), 5)

    def test_unknown_missing_wrong_type_null_array_and_enum_validation(self) -> None:
        calls = [
            {"id": "unknown", "name": "not_registered", "args": {}},
            {"id": "missing", "name": "calculator", "args": {}},
            {"id": "wrong", "name": "calculator", "args": {"expression": 12}},
            {"id": "null_required", "name": "calculator", "args": {"expression": None}},
            {
                "id": "array_item",
                "name": "local_file_search",
                "args": {"query": "agent", "file_types": ["md", 7]},
            },
            {
                "id": "array_enum",
                "name": "local_file_search",
                "args": {"query": "agent", "file_types": ["pdf"]},
            },
            {
                "id": "enum",
                "name": "format_converter",
                "args": {"text": "a: 1", "target_format": "xml"},
            },
            {
                "id": "limit",
                "name": "file_reader",
                "args": {
                    "path": "benchmarks/b2_b3_v1/assets/meeting_notes.txt",
                    "max_chars": 1000000000,
                },
            },
            {
                "id": "nullable",
                "name": "format_converter",
                "args": {
                    "text": "a: 1",
                    "target_format": "json",
                    "output_filename": None,
                },
            },
        ]
        messages = execute_tool_calls(
            calls, str(CONFIG_PATH), "basic_tools", str(self.output_dir / "validation")
        )
        results = [self.decode(message) for message in messages]
        self.assertEqual(
            [result["error"]["code"] if result["error"] else None for result in results],
            [
                "TOOL_NOT_ALLOWED",
                "MISSING_REQUIRED_ARGUMENT",
                "ARGUMENT_TYPE_ERROR",
                "ARGUMENT_NULL_NOT_ALLOWED",
                "ARGUMENT_TYPE_ERROR",
                "ARGUMENT_ENUM_ERROR",
                "ARGUMENT_ENUM_ERROR",
                "INPUT_LIMIT_EXCEEDED",
                None,
            ],
        )
        self.assertEqual(results[-1]["status"], "success")

    def test_multiple_calls_execute_independently_and_preserve_tool_message(self) -> None:
        calls = [
            {"id": "calc", "name": "calculator", "args": {"expression": "6 * 9"}},
            {
                "id": "missing_file",
                "name": "file_reader",
                "args": {"path": "benchmarks/b2_b3_v1/assets/missing.txt"},
            },
            {
                "id": "table",
                "name": "table_analyzer",
                "args": {
                    "path": "benchmarks/b2_b3_v1/assets/inventory.tsv",
                    "describe": False,
                },
            },
        ]
        messages = execute_tool_calls(
            calls, str(CONFIG_PATH), "basic_tools", str(self.output_dir / "multi")
        )
        self.assertEqual([message["tool_call_id"] for message in messages], ["calc", "missing_file", "table"])
        self.assertEqual([message["status"] for message in messages], ["success", "error", "success"])
        for message, call in zip(messages, calls):
            self.assertEqual(set(message), {"role", "tool_call_id", "name", "content", "status"})
            result = self.decode(message)
            self.assertEqual(
                set(result),
                {"skill_name", "status", "input", "output", "error", "latency_ms"},
            )
            self.assertEqual(result["skill_name"], call["name"])

    def test_auto_schema_execution_and_semantic_switch(self) -> None:
        automatic = execute_tool_calls(
            [{"id": "auto", "name": "calculator", "args": {"expression": "7 ** 2"}}],
            str(CONFIG_PATH),
            "basic_tools",
            schema_mode="auto",
        )
        self.assertEqual(self.decode(automatic[0])["output"]["result"], 49)

        config_path = self.write_config(
            lambda config: config["settings"]["features"].update(semantic_search=False)
        )
        disabled = execute_tool_calls(
            [
                {
                    "id": "semantic",
                    "name": "local_file_search",
                    "args": {"query": "agent", "mode": "semantic"},
                }
            ],
            str(config_path),
            "basic_tools",
        )
        self.assertEqual(self.decode(disabled[0])["error"]["code"], "FEATURE_DISABLED")

    def test_high_risk_tools_are_not_in_basic_toolset(self) -> None:
        names = {
            item["function"]["name"]
            for item in get_tools_schema(str(CONFIG_PATH), "basic_tools", schema_mode="manual")
        }
        self.assertNotIn("python_executor", names)
        self.assertNotIn("web_fetcher", names)
        message = execute_tool_calls(
            [{"id": "python", "name": "python_executor", "args": {"code": "result = 1"}}],
            str(CONFIG_PATH),
            "basic_tools",
        )[0]
        self.assertEqual(self.decode(message)["error"]["code"], "TOOL_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()

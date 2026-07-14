from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
for entry in (str(PROJECT_ROOT), str(CODE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from b3.context import ToolExecutionContext
from b3_tool_layer import build_cache_key, execute_tool_calls
from skills.errors import SkillError
import skills.web_fetcher as web_fetcher_module


CONFIG_PATH = PROJECT_ROOT / "configs" / "tools.yaml"


class B3CacheRetryMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def decode(message: dict[str, Any]) -> dict[str, Any]:
        return json.loads(message["content"])

    @staticmethod
    def read_log(directory: Path) -> dict[str, Any]:
        lines = (directory / "tool_call_log.jsonl").read_text(encoding="utf-8").splitlines()
        return json.loads(lines[-1])

    def write_config(self, mutate: Any) -> Path:
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        mutate(config)
        target = self.root / f"tools-{len(list(self.root.glob('tools-*.yaml')))}.yaml"
        target.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return target

    def test_cache_hit_uses_canonical_args_and_rebinds_tool_call_id(self) -> None:
        context = ToolExecutionContext("cache-conversation")
        first_dir = self.root / "first"
        second_dir = self.root / "second"
        first = execute_tool_calls(
            [
                {
                    "id": "first-id",
                    "name": "file_reader",
                    "args": {
                        "path": "benchmarks/b2_b3_v1/assets/meeting_notes.txt",
                        "max_chars": 300,
                    },
                }
            ],
            str(CONFIG_PATH),
            "basic_tools",
            str(first_dir),
            execution_context=context,
            enable_cache=True,
        )[0]
        second = execute_tool_calls(
            [
                {
                    "id": "second-id",
                    "name": "file_reader",
                    "args": {
                        "max_chars": 300,
                        "path": "benchmarks/b2_b3_v1/assets/meeting_notes.txt",
                    },
                }
            ],
            str(CONFIG_PATH),
            "basic_tools",
            str(second_dir),
            execution_context=context,
            enable_cache=True,
        )[0]
        self.assertEqual(first["content"], second["content"])
        self.assertEqual(first["tool_call_id"], "first-id")
        self.assertEqual(second["tool_call_id"], "second-id")
        self.assertFalse(self.read_log(first_dir)["cache_hit"])
        second_log = self.read_log(second_dir)
        self.assertTrue(second_log["cache_hit"])
        self.assertEqual(second_log["attempts"], 0)
        self.assertEqual(context.metrics.snapshot()["cache_hits"], 1)

    def test_cache_is_isolated_by_conversation_and_tool_version(self) -> None:
        context = ToolExecutionContext("default-context")
        call = [{"id": "calc", "name": "calculator", "args": {"expression": "144 / 12"}}]
        with self.assertRaisesRegex(ValueError, "conversation_id does not match"):
            execute_tool_calls(
                call,
                str(CONFIG_PATH),
                "basic_tools",
                str(self.root / "conversation-a"),
                conversation_id="conversation-a",
                execution_context=context,
                enable_cache=True,
            )
        context_a = ToolExecutionContext("conversation-a")
        execute_tool_calls(
            call,
            str(CONFIG_PATH),
            "basic_tools",
            str(self.root / "conversation-a"),
            execution_context=context_a,
            enable_cache=True,
        )
        context_b = ToolExecutionContext("conversation-b")
        execute_tool_calls(
            call,
            str(CONFIG_PATH),
            "basic_tools",
            str(self.root / "conversation-b"),
            execution_context=context_b,
            enable_cache=True,
        )
        self.assertFalse(self.read_log(self.root / "conversation-b")["cache_hit"])

        changed_version = self.write_config(
            lambda config: config["tools"]["calculator"].update(version="9.9.9")
        )
        with self.assertRaisesRegex(ValueError, "context identity"):
            execute_tool_calls(
                call,
                str(changed_version),
                "basic_tools",
                str(self.root / "version-changed"),
                execution_context=context_a,
                enable_cache=True,
            )

    def test_cache_ttl_and_lru_eviction_are_enforced(self) -> None:
        ttl_context = ToolExecutionContext(
            "ttl", cache_max_entries=4, cache_ttl_seconds=0.05
        )
        call = [{"id": "ttl", "name": "calculator", "args": {"expression": "3 * 7"}}]
        execute_tool_calls(
            call, str(CONFIG_PATH), "basic_tools", execution_context=ttl_context, enable_cache=True
        )
        time.sleep(0.08)
        execute_tool_calls(
            call,
            str(CONFIG_PATH),
            "basic_tools",
            str(self.root / "ttl-expired"),
            execution_context=ttl_context,
            enable_cache=True,
        )
        self.assertFalse(self.read_log(self.root / "ttl-expired")["cache_hit"])

        lru_context = ToolExecutionContext("lru", cache_max_entries=1)
        for index, expression in enumerate(("1 + 1", "2 + 2", "1 + 1")):
            execute_tool_calls(
                [{"id": str(index), "name": "calculator", "args": {"expression": expression}}],
                str(CONFIG_PATH),
                "basic_tools",
                str(self.root / f"lru-{index}"),
                execution_context=lru_context,
                enable_cache=True,
            )
        self.assertFalse(self.read_log(self.root / "lru-2")["cache_hit"])

    def test_only_successful_deterministic_read_only_results_are_cached(self) -> None:
        context = ToolExecutionContext("cache-policy")
        side_effect_calls = [
            {
                "id": "convert-1",
                "name": "format_converter",
                "args": {
                    "text": "one\ntwo",
                    "target_format": "markdown",
                    "output_filename": "side.md",
                },
            },
            {
                "id": "convert-2",
                "name": "format_converter",
                "args": {
                    "text": "one\ntwo",
                    "target_format": "markdown",
                    "output_filename": "side.md",
                },
            },
        ]
        directory = self.root / "side-effect"
        messages = execute_tool_calls(
            side_effect_calls,
            str(CONFIG_PATH),
            "basic_tools",
            str(directory),
            execution_context=context,
            enable_cache=True,
        )
        generated = [Path(self.decode(message)["output"]["generated_file_path"]) for message in messages]
        self.assertNotEqual(generated[0], generated[1])
        logs = [json.loads(line) for line in (directory / "tool_call_log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["cache_hit"] for record in logs], [False, False])
        self.assertTrue(all(record["cache_key"] is None for record in logs))

        failed = [{"id": "bad", "name": "calculator", "args": {"expression": "1 / 0"}}]
        execute_tool_calls(
            failed,
            str(CONFIG_PATH),
            "basic_tools",
            execution_context=context,
            enable_cache=True,
        )
        execute_tool_calls(
            failed,
            str(CONFIG_PATH),
            "basic_tools",
            str(self.root / "failed-repeat"),
            execution_context=context,
            enable_cache=True,
        )
        self.assertFalse(self.read_log(self.root / "failed-repeat")["cache_hit"])

    def test_retry_recovers_only_retryable_idempotent_execution_error(self) -> None:
        attempts = {"count": 0}

        def flaky_web_fetcher(url: str) -> dict[str, Any]:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise SkillError(
                    "TRANSIENT_NETWORK_ERROR",
                    "injected transient failure",
                    category="network",
                    retryable=True,
                )
            return {"url": url, "text": "recovered"}

        flaky_web_fetcher.__name__ = "web_fetcher"
        directory = self.root / "retry-success"
        with patch.object(web_fetcher_module, "web_fetcher", flaky_web_fetcher):
            message = execute_tool_calls(
                [{"id": "retry", "name": "web_fetcher", "args": {"url": "https://example.com"}}],
                str(CONFIG_PATH),
                "network_tools",
                str(directory),
                enable_retry=True,
                max_attempts=3,
            )[0]
        self.assertEqual(self.decode(message)["status"], "success")
        self.assertEqual(attempts["count"], 2)
        log = self.read_log(directory)
        self.assertEqual(log["attempts"], 2)
        self.assertEqual(log["retry_count"], 1)

    def test_retry_guards_disabled_nonretryable_validation_and_nonidempotent(self) -> None:
        transient_attempts = {"count": 0}

        def always_transient(url: str) -> dict[str, Any]:
            transient_attempts["count"] += 1
            raise SkillError(
                "TRANSIENT_NETWORK_ERROR",
                "injected transient failure",
                category="network",
                retryable=True,
            )

        always_transient.__name__ = "web_fetcher"
        with patch.object(web_fetcher_module, "web_fetcher", always_transient):
            disabled = execute_tool_calls(
                [{"id": "disabled", "name": "web_fetcher", "args": {"url": "https://example.com"}}],
                str(CONFIG_PATH),
                "network_tools",
                enable_retry=False,
                max_attempts=3,
            )[0]
        self.assertEqual(self.decode(disabled)["status"], "error")
        self.assertEqual(transient_attempts["count"], 1)

        validation = execute_tool_calls(
            [{"id": "validation", "name": "web_fetcher", "args": {"url": 123}}],
            str(CONFIG_PATH),
            "network_tools",
            str(self.root / "validation"),
            enable_retry=True,
            max_attempts=3,
        )[0]
        self.assertEqual(self.decode(validation)["error"]["code"], "ARGUMENT_TYPE_ERROR")
        self.assertEqual(self.read_log(self.root / "validation")["attempts"], 1)

        non_idempotent_config = self.write_config(
            lambda config: config["tools"]["web_fetcher"].update(idempotent=False)
        )
        transient_attempts["count"] = 0
        with patch.object(web_fetcher_module, "web_fetcher", always_transient):
            execute_tool_calls(
                [{"id": "guard", "name": "web_fetcher", "args": {"url": "https://example.com"}}],
                str(non_idempotent_config),
                "network_tools",
                enable_retry=True,
                max_attempts=3,
            )
        self.assertEqual(transient_attempts["count"], 1)

    def test_metrics_artifact_contains_global_and_per_tool_percentiles(self) -> None:
        context = ToolExecutionContext("metrics")
        directory = self.root / "metrics"
        execute_tool_calls(
            [
                {"id": "ok", "name": "calculator", "args": {"expression": "8 * 7"}},
                {"id": "bad", "name": "calculator", "args": {"expression": "1 / 0"}},
            ],
            str(CONFIG_PATH),
            "basic_tools",
            str(directory),
            execution_context=context,
        )
        metrics = json.loads((directory / "tool_metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["total_calls"], 2)
        self.assertEqual(metrics["successes"], 1)
        self.assertEqual(metrics["failures"], 1)
        self.assertIn("p50", metrics["latency_ms"])
        self.assertIn("p95", metrics["latency_ms"])
        self.assertEqual(metrics["by_tool"]["calculator"]["total_calls"], 2)

    def test_cache_key_contains_required_dimensions_and_normalizes_args(self) -> None:
        first = build_cache_key("conversation-1", "file_reader", {"path": "a", "max_chars": 5}, "1.2.3")
        second = build_cache_key("conversation-1", "file_reader", {"max_chars": 5, "path": "a"}, "1.2.3")
        self.assertEqual(first, second)
        parts = first.split("|")
        self.assertEqual(parts[:3], ["conversation-1", "file_reader", "1.2.3"])
        self.assertEqual(len(parts[3]), 64)


if __name__ == "__main__":
    unittest.main()

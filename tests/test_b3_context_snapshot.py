from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
for entry in (str(PROJECT_ROOT), str(CODE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from b3.context import ToolExecutionContext, is_cache_miss
from b3_tool_layer import build_cache_key, execute_tool_calls


CONFIG_PATH = PROJECT_ROOT / "configs" / "tools.yaml"


def _digest(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class B3ContextSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def decode(message: dict[str, Any]) -> dict[str, Any]:
        return json.loads(message["content"])

    def test_snapshot_restores_cache_rebinds_id_and_preserves_metrics_once(self) -> None:
        context = ToolExecutionContext("snapshot-conversation", cache_ttl_seconds=60)
        first = execute_tool_calls(
            [{"id": "before", "name": "calculator", "args": {"expression": "9 * 9"}}],
            str(CONFIG_PATH),
            "basic_tools",
            execution_context=context,
            enable_cache=True,
            batch_id="batch-before",
        )[0]
        snapshot = context.snapshot()
        restored = ToolExecutionContext.restore(
            snapshot,
            expected_identity=snapshot["identity"],
            now_epoch_seconds=snapshot["saved_at_epoch_seconds"],
        )
        self.assertEqual(restored.metrics.snapshot()["total_calls"], 1)
        second = execute_tool_calls(
            [{"id": "after", "name": "calculator", "args": {"expression": "9 * 9"}}],
            str(CONFIG_PATH),
            "basic_tools",
            execution_context=restored,
            enable_cache=True,
            batch_id="batch-after",
        )[0]
        self.assertEqual(first["content"], second["content"])
        self.assertEqual(second["tool_call_id"], "after")
        metrics = restored.metrics.snapshot()
        self.assertEqual(metrics["total_calls"], 2)
        self.assertEqual(metrics["cache_hits"], 1)
        self.assertTrue(restored.get_batch_report("batch-after")["calls"][0]["cache_hit"])

    def test_completed_batch_metadata_restores_without_reexecution(self) -> None:
        context = ToolExecutionContext("completed-batch")
        execute_tool_calls(
            [{"id": "done", "name": "calculator", "args": {"expression": "2 + 3"}}],
            str(CONFIG_PATH),
            "basic_tools",
            execution_context=context,
            batch_id="stable-batch",
        )
        snapshot = context.snapshot()
        restored = ToolExecutionContext.restore(
            snapshot, now_epoch_seconds=snapshot["saved_at_epoch_seconds"]
        )
        report = restored.get_batch_report("stable-batch")
        self.assertEqual(report["calls"][0]["tool_call_id"], "done")
        with self.assertRaisesRegex(ValueError, "cannot be replayed"):
            execute_tool_calls(
                [{"id": "done", "name": "calculator", "args": {"expression": "2 + 3"}}],
                str(CONFIG_PATH),
                "basic_tools",
                execution_context=restored,
                batch_id="stable-batch",
            )
        self.assertEqual(restored.metrics.snapshot()["total_calls"], 1)

    def test_corrupt_and_unknown_snapshot_versions_fail_atomically(self) -> None:
        snapshot = ToolExecutionContext("integrity").snapshot()
        corrupt = copy.deepcopy(snapshot)
        corrupt["conversation_id"] = "tampered"
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            ToolExecutionContext.restore(corrupt)

        unknown = copy.deepcopy(snapshot)
        unknown["format_version"] = 999
        payload = {key: value for key, value in unknown.items() if key != "integrity"}
        unknown["integrity"] = {"sha256": _digest(payload)}
        with self.assertRaisesRegex(ValueError, "unsupported.*version"):
            ToolExecutionContext.restore(unknown)

    def test_ttl_exact_expiry_and_snapshot_size_policy(self) -> None:
        context = ToolExecutionContext("ttl", cache_ttl_seconds=30)
        context.cache.put("small", {"status": "success", "output": {"value": 1}})
        snapshot = context.snapshot()
        remaining = snapshot["cache"]["entries"][0]["remaining_ttl_seconds"]
        restored = ToolExecutionContext.restore(
            snapshot,
            now_epoch_seconds=snapshot["saved_at_epoch_seconds"] + remaining,
        )
        self.assertTrue(is_cache_miss(restored.cache.get("small")))

        context.cache.put("large", {"status": "success", "output": {"text": "x" * 500}})
        limited = context.snapshot(max_cache_entry_bytes=100)
        self.assertEqual(limited["cache"]["persisted_count"], 0)
        self.assertIn("entry_size_limit", {item["reason"] for item in limited["cache"]["skipped"]})

    def test_cache_key_normalization_preserves_json_types_and_array_order(self) -> None:
        base = ("conversation", "calculator")
        ordered_a = build_cache_key(*base, {"value": 1, "items": [1, 2]}, "1")
        ordered_b = build_cache_key(*base, {"items": [1, 2], "value": 1}, "1")
        float_key = build_cache_key(*base, {"value": 1.0, "items": [1, 2]}, "1")
        reversed_key = build_cache_key(*base, {"value": 1, "items": [2, 1]}, "1")
        bool_key = build_cache_key(*base, {"value": True, "items": [1, 2]}, "1")
        self.assertEqual(ordered_a, ordered_b)
        self.assertNotEqual(ordered_a, float_key)
        self.assertNotEqual(ordered_a, reversed_key)
        self.assertNotEqual(ordered_a, bool_key)

    def test_batch_report_has_one_record_per_input_and_stable_missing_ids(self) -> None:
        calls = [
            {"id": "same", "name": "calculator", "args": {"expression": "1 + 1"}},
            {"id": "same", "name": "calculator", "args": {"expression": "2 + 2"}},
            {"name": "missing-tool", "args": {}},
        ]
        first_context = ToolExecutionContext("batch")
        messages = execute_tool_calls(
            calls,
            str(CONFIG_PATH),
            "basic_tools",
            execution_context=first_context,
            batch_id="batch-fixed",
        )
        report = first_context.get_batch_report("batch-fixed")
        self.assertEqual(len(messages), 3)
        self.assertEqual(len(report["calls"]), 3)
        self.assertEqual(
            self.decode(messages[1])["error"]["code"], "DUPLICATE_TOOL_CALL_ID"
        )
        self.assertEqual(self.decode(messages[2])["error"]["code"], "TOOL_NOT_ALLOWED")

        second_context = ToolExecutionContext("batch")
        repeated = execute_tool_calls(
            [calls[2]],
            str(CONFIG_PATH),
            "basic_tools",
            execution_context=second_context,
            batch_id="batch-fixed",
        )[0]
        expected_missing_id = messages[2]["tool_call_id"]
        self.assertNotEqual(repeated["tool_call_id"], expected_missing_id)

        third_context = ToolExecutionContext("batch")
        same_position = execute_tool_calls(
            calls,
            str(CONFIG_PATH),
            "basic_tools",
            execution_context=third_context,
            batch_id="batch-fixed",
        )[2]
        self.assertEqual(same_position["tool_call_id"], expected_missing_id)

    def test_context_identity_rejects_toolset_and_schema_changes(self) -> None:
        context = ToolExecutionContext("identity")
        execute_tool_calls(
            [{"id": "one", "name": "calculator", "args": {"expression": "1 + 1"}}],
            str(CONFIG_PATH),
            "basic_tools",
            execution_context=context,
            schema_mode="manual",
        )
        with self.assertRaisesRegex(ValueError, "context identity"):
            execute_tool_calls(
                [{"id": "two", "name": "calculator", "args": {"expression": "1 + 1"}}],
                str(CONFIG_PATH),
                "sandbox_tools",
                execution_context=context,
                schema_mode="manual",
            )
        with self.assertRaisesRegex(ValueError, "context identity"):
            execute_tool_calls(
                [{"id": "three", "name": "calculator", "args": {"expression": "1 + 1"}}],
                str(CONFIG_PATH),
                "basic_tools",
                execution_context=context,
                schema_mode="auto",
            )


if __name__ == "__main__":
    unittest.main()

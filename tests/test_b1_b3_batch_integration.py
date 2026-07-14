from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
for entry in (str(PROJECT_ROOT), str(CODE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from b1_agent_runtime import _resolve_tool_execution, _validate_runtime_input, run_agent


TOOLS_CONFIG = PROJECT_ROOT / "configs" / "tools.yaml"
MEMORY_CONFIG = PROJECT_ROOT / "configs" / "memory.yaml"
MODEL_CONFIG = PROJECT_ROOT / "configs" / "model.yaml"
SYSTEM_PROMPT = PROJECT_ROOT / "prompts" / "local_tool_agent.txt"


class B1B3BatchIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runtime_payload(self) -> dict:
        return {
            "execution_mode": "integrated",
            "conversation_id": "b1-b3-batch-test",
            "user_input": "Calculate 12 * 12 twice.",
            "system_prompt_path": str(SYSTEM_PROMPT),
            "toolset": "basic_tools",
            "max_turns": 3,
            "save_memory": "none",
            "enable_agent_state": True,
            "enable_checkpoint": False,
            "selected_memory_ids": [],
            "use_global_memory": False,
            "retry_policy": {
                "max_attempts_per_tool": 3,
                "replan_on_tool_error": False,
                "degrade_on_failure": True,
            },
            "tool_execution": {
                "schema_mode": "manual",
                "enable_cache": True,
                "enable_retry": True,
                "max_attempts": 2,
            },
            "ablation": {
                "agent_state": True,
                "checkpoint": True,
                "retry_replan": True,
                "tool_context": True,
                "tool_cache": True,
                "tool_retry": True,
                "replan": True,
            },
        }

    def test_one_b3_batch_per_ai_message_and_cross_round_cache_hit(self) -> None:
        payload = self.runtime_payload()
        input_path = self.root / "runtime.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        responses = iter(
            [
                {
                    "status": "success",
                    "ai_message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "calc-first",
                                "name": "calculator",
                                "args": {"expression": "12 * 12"},
                            }
                        ],
                    },
                },
                {
                    "status": "success",
                    "ai_message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "calc-second",
                                "name": "calculator",
                                "args": {"expression": "12 * 12"},
                            }
                        ],
                    },
                },
                {
                    "status": "success",
                    "ai_message": {
                        "role": "assistant",
                        "content": "The result is 144.",
                        "tool_calls": [],
                    },
                },
            ]
        )

        def fake_generate(*args, **kwargs):
            return next(responses)

        selected_memory = {
            "status": "success",
            "selected_memory_docs": [],
            "total_chars": 0,
            "truncated": False,
        }
        with patch("b1_agent_runtime.generate_ai_message", side_effect=fake_generate), patch(
            "b5_memory.load_memory", return_value=selected_memory
        ):
            result = run_agent(
                str(input_path),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "output"),
                "mock",
            )

        self.assertEqual(result["status"], "success")
        trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        batches = [
            call
            for call in trace["module_calls"]
            if call["module"] == "B3" and call["action"] == "execute_tool_batch"
        ]
        self.assertEqual(len(batches), 2)
        self.assertEqual([item["output"]["call_count"] for item in batches], [1, 1])
        self.assertFalse(batches[0]["output"]["calls"][0]["cache_hit"])
        self.assertTrue(batches[1]["output"]["calls"][0]["cache_hit"])
        self.assertEqual(batches[1]["output"]["calls"][0]["attempts"], 0)
        tool_messages = json.loads(
            (self.root / "output" / "tool_messages.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [message["tool_call_id"] for message in tool_messages],
            ["calc-first", "calc-second"],
        )

    def test_configuration_conflict_and_legacy_retry_source_are_explicit(self) -> None:
        conflict = self.runtime_payload()
        conflict["ablation"]["tool_context"] = False
        with self.assertRaisesRegex(ValueError, "conflicts"):
            _validate_runtime_input(conflict)

        legacy = self.runtime_payload()
        legacy.pop("tool_execution")
        validated = _validate_runtime_input(legacy)
        resolved = _resolve_tool_execution(validated, TOOLS_CONFIG)
        self.assertEqual(resolved["source"]["max_attempts"], "legacy_retry_policy")
        self.assertEqual(resolved["effective"]["max_attempts"], 3)
        self.assertTrue(resolved["warnings"])

    def test_context_ablation_forces_cache_off_when_not_explicitly_requested(self) -> None:
        payload = self.runtime_payload()
        payload["tool_execution"]["enable_cache"] = None
        payload["ablation"]["tool_context"] = False
        validated = _validate_runtime_input(payload)
        resolved = _resolve_tool_execution(validated, TOOLS_CONFIG)
        self.assertFalse(resolved["effective"]["enable_cache"])
        self.assertFalse(resolved["effective"]["persistent_context"])
        self.assertEqual(
            resolved["suppressed_by"]["enable_cache"],
            "ablation.tool_context=false",
        )


if __name__ == "__main__":
    unittest.main()

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

from b1_agent_runtime import _validate_runtime_input, run_agent


TOOLS_CONFIG = PROJECT_ROOT / "configs" / "tools.yaml"
MEMORY_CONFIG = PROJECT_ROOT / "configs" / "memory.yaml"
MODEL_CONFIG = PROJECT_ROOT / "configs" / "model.yaml"
SYSTEM_PROMPT = PROJECT_ROOT / "prompts" / "local_tool_agent.txt"


class B1MultiTurnPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.selected_memory = {
            "status": "success",
            "selected_memory_docs": [],
            "total_chars": 0,
            "truncated": False,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def payload(self) -> dict:
        return {
            "execution_mode": "integrated",
            "conversation_id": "b1-multiturn-test",
            "conversation_turns": [
                {"turn_id": 1, "content": "First question."},
                {
                    "turn_id": 2,
                    "content": "Second question.",
                    "prompt_action": {"operation": "append", "variant": "chinese"},
                },
                {
                    "turn_id": 3,
                    "content": "Third question.",
                    "prompt_action": {"operation": "replace", "variant": "english"},
                },
            ],
            "system_prompt_path": str(SYSTEM_PROMPT),
            "toolset": "basic_tools",
            "max_turns": 2,
            "max_total_llm_calls": 8,
            "max_total_tool_calls": 8,
            "on_user_turn_error": "stop",
            "save_memory": "none",
            "enable_agent_state": True,
            "enable_checkpoint": False,
            "selected_memory_ids": [],
            "use_global_memory": False,
        }

    def write_payload(self, payload: dict, name: str = "runtime.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def response(content: str, route: str) -> dict:
        return {
            "status": "success",
            "ai_message": {
                "role": "assistant",
                "content": content,
                "tool_calls": [],
                "plan": {
                    "plan_id": f"plan-{route}",
                    "steps": [
                        {"step_id": f"step-{route}", "status": "completed"}
                    ],
                },
                "metadata": {
                    "route": route,
                    "route_reason": f"reason-{route}",
                    "route_source": "test",
                },
            },
        }

    def test_legacy_user_input_normalizes_to_one_turn(self) -> None:
        payload = self.payload()
        payload.pop("conversation_turns")
        payload["user_input"] = "Legacy question."
        validated = _validate_runtime_input(payload)
        self.assertEqual(
            validated["conversation_turns"],
            [
                {
                    "turn_id": 1,
                    "content": "Legacy question.",
                    "prompt_action": {"operation": "keep"},
                }
            ],
        )

    def test_rejects_ambiguous_and_invalid_turn_contracts(self) -> None:
        ambiguous = self.payload()
        ambiguous["user_input"] = "also supplied"
        with self.assertRaisesRegex(ValueError, "cannot both"):
            _validate_runtime_input(ambiguous)

        duplicate = self.payload()
        duplicate["conversation_turns"][1]["turn_id"] = 1
        with self.assertRaisesRegex(ValueError, "duplicate"):
            _validate_runtime_input(duplicate)

        descending = self.payload()
        descending["conversation_turns"][1]["turn_id"] = 3
        descending["conversation_turns"][2]["turn_id"] = 2
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            _validate_runtime_input(descending)

    def test_three_turn_prompt_state_and_b4_metadata_are_recorded(self) -> None:
        payload = self.payload()
        input_path = self.write_payload(payload)
        responses = iter(
            [
                self.response("answer-1", "direct"),
                self.response("answer-2", "tool_assisted"),
                self.response("answer-3", "direct"),
            ]
        )
        observed_system_prompts: list[str] = []

        def fake_generate(_model, messages, *_args, **_kwargs):
            observed_system_prompts.append(messages[0]["content"])
            return next(responses)

        with patch(
            "b1_agent_runtime.generate_ai_message", side_effect=fake_generate
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            result = run_agent(
                str(input_path),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "output"),
                "mock",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["final_answer"], "answer-3")
        self.assertEqual(
            [item["answer"] for item in result["user_turn_results"]],
            ["answer-1", "answer-2", "answer-3"],
        )
        self.assertNotIn("请使用中文", observed_system_prompts[0])
        self.assertIn("请使用中文", observed_system_prompts[1])
        self.assertIn("Respond to the current", observed_system_prompts[2])
        self.assertNotIn("请使用中文", observed_system_prompts[2])

        trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        self.assertEqual(trace["completed_user_turn_ids"], [1, 2, 3])
        self.assertEqual([item["version"] for item in trace["prompt_history"]], [1, 2, 3])
        messages = json.loads(Path(result["messages_path"]).read_text(encoding="utf-8"))
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "user", "assistant", "user", "assistant"],
        )
        state = json.loads(
            Path(result["agent_state_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(len(state["route_history"]), 3)
        self.assertEqual(state["active_plan"]["plan_id"], "plan-direct")
        self.assertIsNone(state["current_step_id"])

    def test_continue_policy_isolates_failed_user_turn(self) -> None:
        payload = self.payload()
        payload["conversation_turns"] = payload["conversation_turns"][:2]
        payload["on_user_turn_error"] = "continue"
        payload["retry_policy"] = {
            "max_attempts_per_tool": 1,
            "replan_on_tool_error": False,
            "degrade_on_failure": True,
        }
        input_path = self.write_payload(payload, "continue.json")
        responses = iter(
            [
                {"status": "error", "error": {"type": "Injected", "message": "first failed"}},
                self.response("second succeeded", "direct"),
            ]
        )

        with patch(
            "b1_agent_runtime.generate_ai_message",
            side_effect=lambda *args, **kwargs: next(responses),
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            result = run_agent(
                str(input_path),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "continue-output"),
                "mock",
            )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["final_answer"], "second succeeded")
        self.assertEqual(len(result["user_turn_results"]), 2)
        self.assertEqual(result["user_turn_results"][0]["status"], "degraded_done")
        self.assertEqual(result["user_turn_results"][1]["status"], "success")

    def test_static_prompt_and_stateless_history_ablations_are_observable(self) -> None:
        payload = self.payload()
        payload["conversation_turns"] = payload["conversation_turns"][:2]
        payload["ablation"] = {
            "prompt_dynamic": False,
            "conversation_history": False,
        }
        input_path = self.write_payload(payload, "ablation.json")
        responses = iter(
            [
                self.response("first", "direct"),
                self.response("second", "direct"),
            ]
        )
        observed_messages: list[list[dict]] = []

        def fake_generate(_model, messages, *_args, **_kwargs):
            observed_messages.append(json.loads(json.dumps(messages)))
            return next(responses)

        with patch(
            "b1_agent_runtime.generate_ai_message", side_effect=fake_generate
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            result = run_agent(
                str(input_path),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "ablation-output"),
                "mock",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual([item["role"] for item in observed_messages[1]], ["system", "user"])
        self.assertNotIn("请使用中文", observed_messages[1][0]["content"])
        trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        self.assertEqual(trace["history_compaction"]["source"], "stateless_ablation")
        self.assertEqual(trace["prompt_history"][1]["operation"], "keep")
        full_messages = json.loads(Path(result["messages_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(full_messages), 5)


if __name__ == "__main__":
    unittest.main()

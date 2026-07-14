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

from b1_agent_runtime import (
    POST_TOOL_USER_FORMAT_RULE,
    TOOL_ERROR_MEMORY_RULE,
    _current_turn_tool_state,
    _system_prompt_for_model_call,
    _validate_runtime_input,
    run_agent,
)


TOOLS_CONFIG = PROJECT_ROOT / "configs" / "tools.yaml"
MEMORY_CONFIG = PROJECT_ROOT / "configs" / "memory.yaml"
MODEL_CONFIG = PROJECT_ROOT / "configs" / "model.yaml"
SYSTEM_PROMPT = PROJECT_ROOT / "prompts" / "local_tool_agent.txt"
STALE_ANSWER = "Agent can autonomously perceive, plan, and act without human intervention."


class B1MemoryInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.selected_memory = {
            "status": "success",
            "selected_memory_docs": [
                {
                    "memory_id": "mem_course_001",
                    "memory_type": "global",
                    "content": "Agent systems contain models, tools, memory, and an execution loop.",
                },
                {
                    "memory_id": "mem_conversation_conv_000",
                    "memory_type": "conversation",
                    "content": (
                        "# Conversation conv_000\n\n## Final Answer\n\n"
                        f"{STALE_ANSWER}\n\n## Messages\n\n[old conversation]\n\n"
                        "## Trace\n\n[old trace]"
                    ),
                },
            ],
            "total_chars": 240,
            "truncated": False,
            "errors": [],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def payload(self, mode: str) -> dict:
        return {
            "execution_mode": "integrated",
            "conversation_id": f"memory-injection-{mode}",
            "user_input": "Read docs/agent_intro.txt and summarize three points.",
            "system_prompt_path": str(SYSTEM_PROMPT),
            "selected_memory_ids": ["mem_conversation_conv_000"],
            "use_global_memory": True,
            "memory_injection_mode": mode,
            "toolset": "basic_tools",
            "max_turns": 3,
            "save_memory": "none",
            "enable_agent_state": True,
            "enable_checkpoint": False,
        }

    def write_payload(self, mode: str) -> Path:
        path = self.root / f"runtime-{mode}.json"
        path.write_text(
            json.dumps(self.payload(mode), ensure_ascii=False), encoding="utf-8"
        )
        return path

    @staticmethod
    def tool_request() -> dict:
        return {
            "status": "success",
            "ai_message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_memory_regression",
                        "name": "file_reader",
                        "args": {"path": "docs/agent_intro.txt", "max_chars": 2000},
                    }
                ],
            },
        }

    @staticmethod
    def final_response() -> dict:
        return {
            "status": "success",
            "ai_message": {
                "role": "assistant",
                "content": (
                    "1. Agent systems contain models, tools, memory, and an execution loop.\n"
                    "2. Tools read local files and perform calculations.\n"
                    "3. Memory provides global knowledge and conversation context."
                ),
                "tool_calls": [],
            },
        }

    def run_with_mode(self, mode: str) -> tuple[dict, list[list[dict]], Path]:
        responses = iter([self.tool_request(), self.final_response()])
        observed_messages: list[list[dict]] = []

        def fake_generate(_model, messages, *_args, **_kwargs):
            observed_messages.append(json.loads(json.dumps(messages)))
            return next(responses)

        output = self.root / f"output-{mode}"
        with patch(
            "b1_agent_runtime.generate_ai_message", side_effect=fake_generate
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            result = run_agent(
                str(self.write_payload(mode)),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(output),
                "mock",
            )
        return result, observed_messages, output

    def test_runtime_contract_rejects_unknown_mode(self) -> None:
        payload = self.payload("invalid")
        with self.assertRaisesRegex(ValueError, "memory_injection_mode"):
            _validate_runtime_input(payload)

    def test_phase_aware_removes_memory_after_successful_tool(self) -> None:
        result, observed, output = self.run_with_mode("phase_aware")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(observed), 2)
        self.assertIn(STALE_ANSWER, observed[0][0]["content"])
        self.assertNotIn(STALE_ANSWER, observed[1][0]["content"])
        self.assertNotIn("<memory", observed[1][0]["content"])
        self.assertIn("explicitly numbered items", observed[1][0]["content"])
        self.assertTrue(
            any(
                message.get("role") == "user"
                and POST_TOOL_USER_FORMAT_RULE in message.get("content", "")
                for message in observed[1]
            )
        )
        self.assertEqual(observed[1][-1]["role"], "tool")
        self.assertTrue(
            any(
                message.get("role") == "tool"
                and message.get("status") == "success"
                and message.get("tool_call_id") == "call_memory_regression"
                for message in observed[1]
            )
        )

        decisions = [
            json.loads(line)
            for line in (output / "memory_injection_decisions.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual([item["phase"] for item in decisions], ["planning", "tool_success"])
        self.assertTrue(decisions[0]["memory_injected"])
        self.assertFalse(decisions[1]["memory_injected"])
        self.assertTrue(decisions[1]["post_tool_user_format_reminder"])
        self.assertEqual(
            decisions[1]["suppressed_reason"],
            "successful_tool_result_is_authoritative",
        )

    def test_legacy_and_off_modes_are_observable(self) -> None:
        _, legacy_messages, _ = self.run_with_mode("legacy")
        _, off_messages, _ = self.run_with_mode("off")

        self.assertIn(STALE_ANSWER, legacy_messages[0][0]["content"])
        self.assertIn(STALE_ANSWER, legacy_messages[1][0]["content"])
        self.assertNotIn(STALE_ANSWER, off_messages[0][0]["content"])
        self.assertNotIn(STALE_ANSWER, off_messages[1][0]["content"])

    def test_tool_error_phase_suppresses_memory(self) -> None:
        messages = [
            {"role": "system", "content": "base"},
            {"role": "user", "content": "read a missing file"},
            {
                "role": "tool",
                "tool_call_id": "call_error",
                "name": "file_reader",
                "status": "error",
                "content": "file not found",
            },
        ]
        tool_state = _current_turn_tool_state(messages)
        prompt, decision = _system_prompt_for_model_call(
            "base", f"<memory>{STALE_ANSWER}</memory>", "phase_aware", tool_state
        )

        self.assertEqual(decision["phase"], "tool_error")
        self.assertFalse(decision["memory_injected"])
        self.assertNotIn(STALE_ANSWER, prompt)
        self.assertIn(TOOL_ERROR_MEMORY_RULE, prompt)


if __name__ == "__main__":
    unittest.main()

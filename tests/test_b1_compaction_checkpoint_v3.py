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

import b1_agent_runtime as b1_runtime
from b1_agent_runtime import _query_relevant_history_extract, run_agent
from b4.capability import match_capabilities


TOOLS_CONFIG = PROJECT_ROOT / "configs" / "tools.yaml"
MEMORY_CONFIG = PROJECT_ROOT / "configs" / "memory.yaml"
MODEL_CONFIG = PROJECT_ROOT / "configs" / "model.yaml"


class B1CompactionCheckpointV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prompt = self.root / "system.txt"
        self.prompt.write_text("You are a concise test agent.", encoding="utf-8")
        self.selected_memory = {
            "status": "success",
            "selected_memory_docs": [],
            "total_chars": 0,
            "truncated": False,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def payload(self, resume_from: str | None = None) -> dict:
        return {
            "execution_mode": "integrated",
            "conversation_id": "b1-checkpoint-v3-test",
            "conversation_turns": [
                {"turn_id": 1, "content": "Remember FACT_ALPHA and explain it."},
                {"turn_id": 2, "content": "What fact did I ask you to remember?"},
            ],
            "system_prompt_path": str(self.prompt),
            "toolset": "basic_tools",
            "max_turns": 2,
            "max_total_llm_calls": 8,
            "max_total_tool_calls": 8,
            "on_user_turn_error": "stop",
            "save_memory": "none",
            "enable_agent_state": True,
            "enable_checkpoint": True,
            "resume_from_checkpoint": resume_from,
            "selected_memory_ids": [],
            "use_global_memory": False,
            "history_compaction": {
                "enabled": True,
                "trigger_chars": 1500,
                "target_chars": 800,
                "preserve_recent_turns": 1,
                "mode": "deterministic",
                "fallback": "deterministic",
            },
        }

    def write_payload(self, payload: dict, name: str) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def response(content: str) -> dict:
        return {
            "status": "success",
            "ai_message": {
                "role": "assistant",
                "content": content,
                "tool_calls": [],
            },
        }

    def run_with_responses(
        self,
        input_path: Path,
        output_name: str,
        responses,
    ) -> dict:
        with patch(
            "b1_agent_runtime.generate_ai_message", side_effect=responses
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            return run_agent(
                str(input_path),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / output_name),
                "mock",
            )

    def test_deterministic_compaction_preserves_full_transcript(self) -> None:
        input_path = self.write_payload(self.payload(), "compact.json")
        observed_messages: list[list[dict]] = []
        long_answer = "FACT_ALPHA " + ("context " * 700)
        responses = iter([self.response(long_answer), self.response("FACT_ALPHA")])

        def fake_generate(_model, messages, *_args, **_kwargs):
            observed_messages.append(json.loads(json.dumps(messages)))
            return next(responses)

        result = self.run_with_responses(
            input_path,
            "compact-output",
            fake_generate,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(observed_messages), 2)
        self.assertFalse(
            any("B1_HISTORY_SUMMARY" in item.get("content", "") for item in observed_messages[0])
        )
        self.assertTrue(
            any("B1_HISTORY_SUMMARY" in item.get("content", "") for item in observed_messages[1])
        )
        self.assertLess(
            sum(len(item.get("content", "")) for item in observed_messages[1]),
            len(long_answer),
        )
        full_lines = Path(result["messages_full_path"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(full_lines), 5)
        self.assertIn(long_answer, Path(result["messages_path"]).read_text(encoding="utf-8"))
        trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        self.assertTrue(trace["history_compaction"]["active"])
        self.assertEqual(trace["history_compaction"]["source"], "deterministic")
        self.assertGreater(trace["full_message_count"], trace["model_message_count"])

    def test_summary_control_message_does_not_request_a_tool_capability(self) -> None:
        match = match_capabilities(
            [
                {"role": "system", "content": "Summarize 1/9 file and calculation history."},
                {
                    "role": "user",
                    "content": "Condense the supplied context into a factual digest focused on the current question.",
                },
            ],
            [],
        )
        self.assertFalse(match.missing_required)

    def test_query_relevant_extract_keeps_middle_evidence(self) -> None:
        source = "\n".join(
            ["unrelated context"] * 100
            + ["[D14:4] Audrey: I take them to the park twice a week for practice."]
            + ["more unrelated context"] * 100
        )
        extract = _query_relevant_history_extract(
            source,
            "How often does Audrey take her pups to the park for practice?",
            600,
        )
        self.assertIn("twice a week", extract)

    def test_llm_summary_failure_uses_declared_fallback(self) -> None:
        payload = self.payload()
        payload["history_compaction"]["mode"] = "llm"
        input_path = self.write_payload(payload, "fallback.json")
        long_answer = "FACT_ALPHA " + ("history " * 700)
        responses = iter(
            [
                self.response(long_answer),
                {"status": "error", "error": {"type": "Injected", "message": "summary failed"}},
                self.response("FACT_ALPHA"),
            ]
        )

        def fake_generate(*args, **kwargs):
            result = next(responses)
            if result.get("status") == "error":
                summary_messages = args[1]
                self.assertTrue(
                    summary_messages[-1]["content"].startswith("Condense the supplied context")
                )
            return result

        result = self.run_with_responses(
            input_path,
            "fallback-output",
            fake_generate,
        )

        trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        self.assertEqual(trace["status"], "success")
        self.assertEqual(
            trace["history_compaction"]["source"],
            "deterministic_fallback",
        )
        self.assertTrue(any("fallback used" in item for item in trace["warnings"]))

    def create_turn_completed_checkpoint(self) -> Path:
        input_path = self.write_payload(self.payload(), "interrupt.json")
        original_write_checkpoint = b1_runtime._write_checkpoint
        interrupted = {"done": False}

        def interrupt_after_completed(*args, **kwargs):
            record = original_write_checkpoint(*args, **kwargs)
            state = args[0]
            if (
                not interrupted["done"]
                and state is not None
                and state.phase == "USER_TURN_COMPLETED"
                and state.current_user_turn_id == 1
            ):
                interrupted["done"] = True
                raise KeyboardInterrupt("interrupt after completed user turn")
            return record

        with patch(
            "b1_agent_runtime.generate_ai_message",
            return_value=self.response("first answer FACT_ALPHA"),
        ), patch("b5_memory.load_memory", return_value=self.selected_memory), patch(
            "b1_agent_runtime._write_checkpoint",
            side_effect=interrupt_after_completed,
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_agent(
                    str(input_path),
                    str(TOOLS_CONFIG),
                    str(MEMORY_CONFIG),
                    str(MODEL_CONFIG),
                    str(self.root / "interrupt-output"),
                    "mock",
                )
        checkpoints = sorted((self.root / "interrupt-output" / "checkpoints").glob("*.json"))
        self.assertTrue(checkpoints)
        checkpoint = checkpoints[-1]
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(data["checkpoint_format_version"], 3)
        self.assertEqual(data["recent_phase"], "USER_TURN_COMPLETED")
        return checkpoint

    def test_resume_after_completed_turn_does_not_repeat_first_turn(self) -> None:
        checkpoint = self.create_turn_completed_checkpoint()
        resume_input = self.write_payload(
            self.payload(str(checkpoint)),
            "resume.json",
        )
        model_calls = {"count": 0}

        def second_turn_only(*args, **kwargs):
            model_calls["count"] += 1
            return self.response("FACT_ALPHA")

        result = self.run_with_responses(
            resume_input,
            "resume-output",
            second_turn_only,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(model_calls["count"], 1)
        self.assertEqual(
            [item["user_turn_id"] for item in result["user_turn_results"]],
            [1, 2],
        )
        messages = json.loads(Path(result["messages_path"]).read_text(encoding="utf-8"))
        self.assertEqual(
            [item.get("user_turn_id") for item in messages if item.get("role") == "user"],
            [1, 2],
        )

    def test_v3_prompt_change_is_rejected_before_model_call(self) -> None:
        checkpoint = self.create_turn_completed_checkpoint()
        self.prompt.write_text("Changed system prompt.", encoding="utf-8")
        resume_input = self.write_payload(
            self.payload(str(checkpoint)),
            "prompt-mismatch.json",
        )

        with patch("b1_agent_runtime.generate_ai_message") as model_call:
            with self.assertRaisesRegex(ValueError, "base_system_prompt_sha256"):
                run_agent(
                    str(resume_input),
                    str(TOOLS_CONFIG),
                    str(MEMORY_CONFIG),
                    str(MODEL_CONFIG),
                    str(self.root / "prompt-mismatch-output"),
                    "mock",
                )
        model_call.assert_not_called()

    def test_v3_execution_policy_change_is_rejected_before_model_call(self) -> None:
        checkpoint = self.create_turn_completed_checkpoint()
        payload = self.payload(str(checkpoint))
        payload["ablation"] = {"conversation_history": False}
        resume_input = self.write_payload(payload, "policy-mismatch.json")

        with patch("b1_agent_runtime.generate_ai_message") as model_call:
            with self.assertRaisesRegex(ValueError, "execution_policy_sha256"):
                run_agent(
                    str(resume_input),
                    str(TOOLS_CONFIG),
                    str(MEMORY_CONFIG),
                    str(MODEL_CONFIG),
                    str(self.root / "policy-mismatch-output"),
                    "mock",
                )
        model_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()

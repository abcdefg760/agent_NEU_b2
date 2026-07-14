from __future__ import annotations

import copy
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

from b1_agent_runtime import run_agent
import b1_agent_runtime as b1_runtime


TOOLS_CONFIG = PROJECT_ROOT / "configs" / "tools.yaml"
MEMORY_CONFIG = PROJECT_ROOT / "configs" / "memory.yaml"
MODEL_CONFIG = PROJECT_ROOT / "configs" / "model.yaml"
SYSTEM_PROMPT = PROJECT_ROOT / "prompts" / "local_tool_agent.txt"


class B1B3CheckpointTests(unittest.TestCase):
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

    def payload(self, resume_from: str | None = None) -> dict:
        return {
            "execution_mode": "integrated",
            "conversation_id": "checkpoint-conversation",
            "user_input": "Calculate 15 * 15.",
            "system_prompt_path": str(SYSTEM_PROMPT),
            "toolset": "basic_tools",
            "max_turns": 4,
            "save_memory": "none",
            "enable_agent_state": True,
            "enable_checkpoint": True,
            "resume_from_checkpoint": resume_from,
            "selected_memory_ids": [],
            "use_global_memory": False,
            "retry_policy": {
                "max_attempts_per_tool": 1,
                "replan_on_tool_error": False,
                "degrade_on_failure": True,
            },
            "tool_execution": {
                "schema_mode": "manual",
                "enable_cache": True,
                "enable_retry": False,
                "max_attempts": 1,
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

    def write_input(self, name: str, payload: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def tool_request(call_id: str) -> dict:
        return {
            "status": "success",
            "ai_message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "name": "calculator",
                        "args": {"expression": "15 * 15"},
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
                "content": "The result is 225.",
                "tool_calls": [],
            },
        }

    @staticmethod
    def format_request(call_id: str) -> dict:
        return {
            "status": "success",
            "ai_message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "name": "format_converter",
                        "args": {
                            "text": "alpha\nbeta",
                            "target_format": "markdown",
                            "output_filename": "checkpoint.md",
                        },
                    }
                ],
            },
        }

    def create_completed_batch_checkpoint(self) -> Path:
        input_path = self.write_input("initial.json", self.payload())
        calls = {"count": 0}

        def crash_after_first_batch(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return self.tool_request("before-crash")
            raise KeyboardInterrupt("injected process interruption")

        with patch(
            "b1_agent_runtime.generate_ai_message", side_effect=crash_after_first_batch
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            with self.assertRaises(KeyboardInterrupt):
                run_agent(
                    str(input_path),
                    str(TOOLS_CONFIG),
                    str(MEMORY_CONFIG),
                    str(MODEL_CONFIG),
                    str(self.root / "initial-output"),
                    "mock",
                )
        checkpoints = sorted((self.root / "initial-output" / "checkpoints").glob("*.json"))
        self.assertTrue(checkpoints)
        checkpoint = checkpoints[-1]
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(payload["tool_batch_state"]["status"], "completed")
        self.assertIsNotNone(payload["tool_execution_context"])
        return checkpoint

    def test_restore_hits_persisted_cache_rebinds_id_and_does_not_double_metrics(self) -> None:
        checkpoint = self.create_completed_batch_checkpoint()
        resume_input = self.write_input("resume.json", self.payload(str(checkpoint)))
        responses = iter([self.tool_request("after-resume"), self.final_response()])

        with patch(
            "b1_agent_runtime.generate_ai_message", side_effect=lambda *a, **k: next(responses)
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            result = run_agent(
                str(resume_input),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "resumed-output"),
                "mock",
            )

        self.assertEqual(result["status"], "success")
        trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        self.assertTrue(trace["context_restored"])
        resumed_batches = [
            call
            for call in trace["module_calls"]
            if call["module"] == "B3"
            and call["action"] == "execute_tool_batch"
            and call["input"]["tool_call_ids"] == ["after-resume"]
        ]
        self.assertEqual(len(resumed_batches), 1)
        call_report = resumed_batches[0]["output"]["calls"][0]
        self.assertTrue(call_report["cache_hit"])
        self.assertEqual(call_report["attempts"], 0)
        self.assertEqual(trace["tool_execution_summary"]["total_calls"], 2)
        self.assertEqual(trace["tool_execution_summary"]["cache_hits"], 1)
        restored_messages = json.loads(
            (self.root / "resumed-output" / "tool_messages.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [message["tool_call_id"] for message in restored_messages],
            ["before-crash", "after-resume"],
        )

    def test_corrupt_snapshot_fails_before_model_execution(self) -> None:
        checkpoint = self.create_completed_batch_checkpoint()
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        data["tool_execution_context"]["conversation_id"] = "tampered"
        corrupt = self.root / "corrupt.json"
        corrupt.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        resume_input = self.write_input("corrupt-resume.json", self.payload(str(corrupt)))

        with patch("b1_agent_runtime.generate_ai_message") as model_call, patch(
            "b5_memory.load_memory", return_value=self.selected_memory
        ):
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                run_agent(
                    str(resume_input),
                    str(TOOLS_CONFIG),
                    str(MEMORY_CONFIG),
                    str(MODEL_CONFIG),
                    str(self.root / "corrupt-output"),
                    "mock",
                )
        model_call.assert_not_called()

    def test_legacy_checkpoint_without_context_resumes_with_warning(self) -> None:
        checkpoint = self.create_completed_batch_checkpoint()
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        data.pop("tool_execution_context")
        data["checkpoint_format_version"] = 1
        legacy = self.root / "legacy.json"
        legacy.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        resume_input = self.write_input("legacy-resume.json", self.payload(str(legacy)))

        with patch(
            "b1_agent_runtime.generate_ai_message", return_value=self.final_response()
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            result = run_agent(
                str(resume_input),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "legacy-output"),
                "mock",
            )
        trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        self.assertFalse(trace["context_restored"])
        self.assertTrue(any("empty context" in warning for warning in trace["warnings"]))

    def test_unknown_checkpoint_version_is_rejected(self) -> None:
        checkpoint = self.create_completed_batch_checkpoint()
        data = copy.deepcopy(json.loads(checkpoint.read_text(encoding="utf-8")))
        data["checkpoint_format_version"] = 999
        unknown = self.root / "unknown.json"
        unknown.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        resume_input = self.write_input("unknown-resume.json", self.payload(str(unknown)))
        with self.assertRaisesRegex(ValueError, "unsupported B1 checkpoint"):
            run_agent(
                str(resume_input),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "unknown-output"),
                "mock",
            )

    def test_pending_batch_resumes_with_same_batch_id(self) -> None:
        initial_input = self.write_input("pending-initial.json", self.payload())
        original_write_checkpoint = b1_runtime._write_checkpoint

        def interrupt_after_pending(*args, **kwargs):
            record = original_write_checkpoint(*args, **kwargs)
            runtime = args[1]
            batch = runtime.get("_tool_batch_state")
            if isinstance(batch, dict) and batch.get("status") == "pending":
                raise KeyboardInterrupt("injected interruption after pending checkpoint")
            return record

        with patch(
            "b1_agent_runtime.generate_ai_message",
            return_value=self.tool_request("pending-call"),
        ), patch("b5_memory.load_memory", return_value=self.selected_memory), patch(
            "b1_agent_runtime._write_checkpoint", side_effect=interrupt_after_pending
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_agent(
                    str(initial_input),
                    str(TOOLS_CONFIG),
                    str(MEMORY_CONFIG),
                    str(MODEL_CONFIG),
                    str(self.root / "pending-initial-output"),
                    "mock",
                )
        checkpoint = sorted(
            (self.root / "pending-initial-output" / "checkpoints").glob("*.json")
        )[-1]
        checkpoint_data = json.loads(checkpoint.read_text(encoding="utf-8"))
        expected_batch_id = checkpoint_data["tool_batch_state"]["batch_id"]
        self.assertEqual(checkpoint_data["tool_batch_state"]["status"], "pending")

        resume_input = self.write_input(
            "pending-resume.json", self.payload(str(checkpoint))
        )
        with patch(
            "b1_agent_runtime.generate_ai_message", return_value=self.final_response()
        ), patch("b5_memory.load_memory", return_value=self.selected_memory):
            result = run_agent(
                str(resume_input),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "pending-resumed-output"),
                "mock",
            )
        trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        batches = [
            call
            for call in trace["module_calls"]
            if call["module"] == "B3" and call["action"] == "execute_tool_batch"
        ]
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["input"]["batch_id"], expected_batch_id)
        self.assertEqual(batches[0]["output"]["calls"][0]["attempts"], 1)

    def test_running_non_read_only_batch_is_not_replayed(self) -> None:
        initial_input = self.write_input("running-initial.json", self.payload())
        original_write_checkpoint = b1_runtime._write_checkpoint

        def interrupt_after_running(*args, **kwargs):
            record = original_write_checkpoint(*args, **kwargs)
            runtime = args[1]
            batch = runtime.get("_tool_batch_state")
            if isinstance(batch, dict) and batch.get("status") == "running":
                raise KeyboardInterrupt("injected interruption after running checkpoint")
            return record

        with patch(
            "b1_agent_runtime.generate_ai_message",
            return_value=self.format_request("format-running"),
        ), patch("b5_memory.load_memory", return_value=self.selected_memory), patch(
            "b1_agent_runtime._write_checkpoint", side_effect=interrupt_after_running
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_agent(
                    str(initial_input),
                    str(TOOLS_CONFIG),
                    str(MEMORY_CONFIG),
                    str(MODEL_CONFIG),
                    str(self.root / "running-initial-output"),
                    "mock",
                )
        checkpoint = sorted(
            (self.root / "running-initial-output" / "checkpoints").glob("*.json")
        )[-1]
        checkpoint_data = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(checkpoint_data["tool_batch_state"]["status"], "running")

        resume_input = self.write_input(
            "running-resume.json", self.payload(str(checkpoint))
        )
        with patch("b1_agent_runtime.generate_ai_message") as model_call, patch(
            "b5_memory.load_memory", return_value=self.selected_memory
        ), patch("b3_tool_layer.execute_tool_calls") as b3_execute:
            result = run_agent(
                str(resume_input),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(self.root / "running-resumed-output"),
                "mock",
            )
        model_call.assert_not_called()
        b3_execute.assert_not_called()
        self.assertEqual(result["status"], "degraded_done")
        messages = json.loads(
            (self.root / "running-resumed-output" / "tool_messages.json").read_text(
                encoding="utf-8"
            )
        )
        error = json.loads(messages[-1]["content"])["error"]
        self.assertEqual(error["code"], "EXECUTION_STATE_UNCERTAIN")


if __name__ == "__main__":
    unittest.main()

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

from b1_batch_runner import run_batch


class B1BatchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def task(self, task_id: str, conversation_id: str) -> dict:
        return {
            "task_id": task_id,
            "runtime_input": {
                "conversation_id": conversation_id,
                "user_input": f"input for {task_id}",
            },
        }

    def write_batch(self, payload: dict, name: str = "batch.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_continue_on_error_isolates_tasks_and_reports_counts(self) -> None:
        payload = {
            "continue_on_error": True,
            "tasks": [
                self.task("task-1", "conversation-1"),
                self.task("task-2", "conversation-2"),
                self.task("task-3", "conversation-3"),
            ],
        }
        batch_path = self.write_batch(payload)
        calls: list[str] = []

        def fake_run(input_path, _tools, _memory, _model, outdir, _mode):
            runtime = json.loads(Path(input_path).read_text(encoding="utf-8"))
            calls.append(runtime["conversation_id"])
            if runtime["conversation_id"] == "conversation-2":
                raise ValueError("injected task failure")
            return {
                "conversation_id": runtime["conversation_id"],
                "status": "success",
                "final_answer": "ok",
                "trace_path": str(Path(outdir) / "trace.json"),
            }

        with patch("b1_batch_runner.run_agent", side_effect=fake_run):
            report = run_batch(str(batch_path), str(self.root / "output"))

        self.assertEqual(calls, ["conversation-1", "conversation-2", "conversation-3"])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["success_count"], 2)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["skipped_count"], 0)
        self.assertEqual(len(Path(report["batch_log_path"]).read_text(encoding="utf-8").splitlines()), 3)

    def test_stop_on_error_does_not_execute_remaining_task(self) -> None:
        payload = {
            "continue_on_error": False,
            "tasks": [
                self.task("task-1", "conversation-1"),
                self.task("task-2", "conversation-2"),
                self.task("task-3", "conversation-3"),
            ],
        }
        batch_path = self.write_batch(payload, "stop.json")
        calls = {"count": 0}

        def fake_run(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("stop here")
            return {"status": "success", "final_answer": "ok"}

        with patch("b1_batch_runner.run_agent", side_effect=fake_run):
            report = run_batch(str(batch_path), str(self.root / "stop-output"))

        self.assertEqual(calls["count"], 2)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["skipped_count"], 1)
        self.assertTrue(report["stopped_early"])

    def test_duplicate_identifiers_and_output_paths_fail_before_execution(self) -> None:
        cases = [
            {
                "tasks": [
                    self.task("same", "conversation-1"),
                    self.task("same", "conversation-2"),
                ]
            },
            {
                "tasks": [
                    self.task("task-1", "same-conversation"),
                    self.task("task-2", "same-conversation"),
                ]
            },
            {
                "tasks": [
                    {**self.task("task-1", "conversation-1"), "output_subdir": "same"},
                    {**self.task("task-2", "conversation-2"), "output_subdir": "same"},
                ]
            },
        ]
        for index, payload in enumerate(cases):
            with self.subTest(index=index), patch("b1_batch_runner.run_agent") as run:
                batch_path = self.write_batch(payload, f"invalid-{index}.json")
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    run_batch(str(batch_path), str(self.root / f"invalid-output-{index}"))
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

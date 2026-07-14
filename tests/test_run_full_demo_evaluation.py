from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
for entry in (str(PROJECT_ROOT), str(CODE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from run_full_demo import _evaluate_demo


class FullDemoEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.outdir = Path(self.temporary.name)
        self.expectations = json.loads(
            (PROJECT_ROOT / "data" / "run_full_demo_expectations.json").read_text(
                encoding="utf-8"
            )
        )
        (self.outdir / "tool_messages.json").write_text(
            json.dumps(
                [
                    {
                        "role": "tool",
                        "tool_call_id": "call_001",
                        "name": "file_reader",
                        "status": "success",
                        "content": "file content",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (self.outdir / "selected_memory.json").write_text(
            json.dumps(
                {
                    "selected_memory_docs": [
                        {"memory_id": "mem_course_001"},
                        {"memory_id": "mem_conversation_conv_000"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.outdir / "memory_injection_decisions.jsonl").write_text(
            json.dumps(
                {
                    "phase": "tool_success",
                    "memory_injected": False,
                    "suppressed_reason": "successful_tool_result_is_authoritative",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def correct_answer() -> str:
        return (
            "1. Agent 系统由模型、工具、记忆和执行循环组成。\n"
            "2. 工具能够读取本地文件并执行计算。\n"
            "3. Memory 提供全局知识、历史对话上下文。"
        )

    def test_correct_grounded_answer_passes(self) -> None:
        evaluation = _evaluate_demo(
            {"status": "success", "final_answer": self.correct_answer()},
            self.outdir,
            self.expectations,
        )
        self.assertEqual(evaluation["status"], "PASS")

    def test_stale_answer_fails_content_acceptance(self) -> None:
        evaluation = _evaluate_demo(
            {
                "status": "success",
                "final_answer": (
                    "1. Agent 可以自主感知环境。\n"
                    "2. Agent 无需人类持续干预。\n"
                    "3. Agent 可用于机器人、客户服务。"
                ),
            },
            self.outdir,
            self.expectations,
        )
        self.assertEqual(evaluation["status"], "FAIL")
        self.assertFalse(evaluation["checks"]["forbidden_stale_facts"]["passed"])
        self.assertTrue(evaluation["checks"]["pipeline"]["passed"])


if __name__ == "__main__":
    unittest.main()

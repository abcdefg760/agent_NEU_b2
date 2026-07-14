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

from b1_advanced_runner import (
    _load_case_index,
    _runtime_from_case,
    run_benchmark,
    score_case,
)
from b1_agent_runtime import _validate_runtime_input


BENCHMARK_ROOT = PROJECT_ROOT / "data" / "b1_advanced"
SYSTEM_PROMPT = PROJECT_ROOT / "prompts" / "local_tool_agent.txt"
TOOLS_CONFIG = PROJECT_ROOT / "configs" / "tools.yaml"
MEMORY_CONFIG = PROJECT_ROOT / "configs" / "memory.yaml"
MODEL_CONFIG = PROJECT_ROOT / "configs" / "model.yaml"


class B1AdvancedBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.case_index, self.fixture_ids = _load_case_index(BENCHMARK_ROOT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_case_index_matches_frozen_public_and_fixture_counts(self) -> None:
        self.assertEqual(len(self.case_index), 127)
        self.assertEqual(len(self.fixture_ids), 19)

    def test_runtime_adapter_excludes_gold_and_accepts_dataset_prompt_operations(self) -> None:
        for case_id in ("locomo_cross_0001", "tatoeba_prompt_0001", "mgsm_zh_calc_0001"):
            with self.subTest(case_id=case_id):
                runtime = _runtime_from_case(
                    self.case_index[case_id],
                    BENCHMARK_ROOT,
                    SYSTEM_PROMPT,
                    4,
                    "off",
                )
                serialized = json.dumps(runtime, ensure_ascii=False)
                self.assertNotIn('"expected"', serialized)
                self.assertNotIn('"scoring"', serialized)
                validated = _validate_runtime_input(runtime)
                self.assertTrue(validated["conversation_turns"])
                self.assertFalse(validated["history_compaction"]["enabled"])
                if case_id == "locomo_cross_0001":
                    self.assertTrue(
                        all("primary_file:" in turn["content"] for turn in validated["conversation_turns"])
                    )

    def test_numeric_json_and_fact_scorers_use_post_run_gold_only(self) -> None:
        numeric = self.case_index["mgsm_zh_calc_0001"]
        numeric_result = {
            "status": "success",
            "final_answer": "答案是 15。",
            "user_turn_results": [{"answer": "答案是 15。"}],
        }
        self.assertTrue(score_case(numeric, numeric_result)["passed"])

        batch_case = self.case_index["tatoeba_batch_0001"]
        expected_items = batch_case["expected"]["answer"]
        batch_answer = json.dumps(
            [
                {"chinese": item["chinese"], "english": item["english_candidates"][0]}
                for item in expected_items
            ],
            ensure_ascii=False,
        )
        batch_result = {
            "status": "success",
            "final_answer": batch_answer,
            "user_turn_results": [{"answer": batch_answer}],
        }
        self.assertTrue(score_case(batch_case, batch_result)["passed"])

        summary_case = self.case_index["locomo_summary_0001"]
        fact_answer = " ".join(summary_case["expected"]["required_facts"])
        fact_result = {
            "status": "success",
            "final_answer": fact_answer,
            "user_turn_results": [{"answer": fact_answer}],
        }
        self.assertTrue(score_case(summary_case, fact_result)["passed"])

    def test_single_public_case_runs_through_batch_and_is_scored(self) -> None:
        def fake_batch(batch_path: str, outdir: str) -> dict:
            payload = json.loads(Path(batch_path).read_text(encoding="utf-8"))
            task = payload["tasks"][0]
            runtime = task["runtime_input"]
            self.assertNotIn("expected", runtime)
            result = {
                "conversation_id": runtime["conversation_id"],
                "status": "success",
                "final_answer": "15",
                "user_turn_results": [{"user_turn_id": 1, "answer": "15", "status": "success"}],
            }
            return {
                "status": "success",
                "batch_report_path": str(Path(outdir) / "batch_report.json"),
                "tasks": [
                    {
                        "task_id": task["task_id"],
                        "runtime_status": "success",
                        "output_dir": str(Path(outdir) / task["task_id"]),
                        "result": result,
                        "error": None,
                    }
                ],
            }

        with patch("b1_advanced_runner.run_batch", side_effect=fake_batch):
            report = run_benchmark(
                str(BENCHMARK_ROOT),
                str(self.root / "benchmark"),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(SYSTEM_PROMPT),
                "mock",
                case_ids=["mgsm_zh_calc_0001"],
            )

        self.assertEqual(report["public_executed_count"], 1)
        self.assertEqual(report["passed_count"], 1)
        self.assertEqual(report["pass_rate"], 1.0)

    def test_fixture_cases_are_linked_to_fault_harness_not_natural_prompt_runs(self) -> None:
        fixture_id = sorted(self.fixture_ids)[0]
        with patch("b1_advanced_runner.run_batch") as batch:
            report = run_benchmark(
                str(BENCHMARK_ROOT),
                str(self.root / "fixture"),
                str(TOOLS_CONFIG),
                str(MEMORY_CONFIG),
                str(MODEL_CONFIG),
                str(SYSTEM_PROMPT),
                "mock",
                case_ids=[fixture_id],
            )
        batch.assert_not_called()
        self.assertEqual(report["fixture_harness_count"], 1)
        self.assertEqual(report["results"][0]["runtime_status"], "fixture_harness_required")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

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

from b1_ablation_runner import run_ablation


class B1AblationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_profiles_use_same_cases_and_report_metric_deltas(self) -> None:
        calls: list[dict] = []

        def fake_benchmark(*args, **kwargs):
            profile = kwargs["ablation_profile"]
            calls.append(
                {
                    "profile": profile,
                    "case_ids": list(kwargs["case_ids"]),
                    "compaction_mode": kwargs["compaction_mode"],
                }
            )
            enhanced = profile == "enhanced"
            score = 0.9 if enhanced else 0.7
            return {
                "status": "success" if enhanced else "partial",
                "selected_count": 2,
                "passed_count": 2 if enhanced else 1,
                "pass_rate": 1.0 if enhanced else 0.5,
                "benchmark_report_path": str(self.root / profile / "benchmark_report.json"),
                "results": [
                    {
                        "score": {"score": score, "passed": enhanced},
                        "metrics": {
                            "elapsed_ms": 100 if enhanced else 80,
                            "llm_call_count": 2,
                            "tool_rounds_used": 1,
                            "cache_hits": 1 if enhanced else 0,
                            "tool_retries": 0,
                            "full_chars": 1000,
                            "model_chars": 400 if enhanced else 1000,
                            "summary_llm_call_count": 1 if enhanced else 0,
                        },
                    },
                    {
                        "score": {"score": score, "passed": enhanced},
                        "metrics": {
                            "elapsed_ms": 120 if enhanced else 90,
                            "llm_call_count": 2,
                            "tool_rounds_used": 1,
                            "cache_hits": 0,
                            "tool_retries": 0,
                            "full_chars": 1200,
                            "model_chars": 500 if enhanced else 1200,
                            "summary_llm_call_count": 1 if enhanced else 0,
                        },
                    },
                ],
            }

        with patch("b1_ablation_runner.run_benchmark", side_effect=fake_benchmark):
            report = run_ablation(
                "benchmark",
                str(self.root / "output"),
                "tools",
                "memory",
                "model",
                "prompt",
                "mock",
                case_ids=["case-1", "case-2"],
                profiles=["enhanced", "no_compaction"],
            )

        self.assertEqual([item["profile"] for item in calls], ["enhanced", "no_compaction"])
        self.assertTrue(all(item["case_ids"] == ["case-1", "case-2"] for item in calls))
        self.assertEqual(report["baseline_profile"], "enhanced")
        no_compaction = next(item for item in report["profiles"] if item["profile"] == "no_compaction")
        self.assertEqual(no_compaction["delta_vs_enhanced"]["pass_rate"], -0.5)
        self.assertEqual(no_compaction["delta_vs_enhanced"]["mean_score"], -0.2)
        self.assertGreater(no_compaction["delta_vs_enhanced"]["mean_model_chars"], 0)
        self.assertTrue(Path(report["ablation_report_path"]).exists())

    def test_duplicate_or_unknown_profiles_fail_before_benchmark(self) -> None:
        for profiles in (["enhanced", "enhanced"], ["unknown"]):
            with self.subTest(profiles=profiles), patch(
                "b1_ablation_runner.run_benchmark"
            ) as benchmark:
                with self.assertRaises(ValueError):
                    run_ablation(
                        "benchmark",
                        str(self.root / "invalid"),
                        "tools",
                        "memory",
                        "model",
                        "prompt",
                        "mock",
                        case_ids=["case-1"],
                        profiles=profiles,
                    )
                benchmark.assert_not_called()


if __name__ == "__main__":
    unittest.main()

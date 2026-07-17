from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
for entry in (str(PROJECT_ROOT), str(CODE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from b2_run_skill import run_skill
from b2_b3_evaluate import _retrieval_metrics
from skills.errors import SkillError
from skills.file_reader import file_reader
from skills.list_directory import list_directory
from skills.local_file_search import _normalize_scores, _semantic_scores, local_file_search
from skills.python_executor import python_executor
from skills.read_and_convert import read_and_convert
from skills.web_fetcher import web_fetcher


class DotVector(list[float]):
    def __matmul__(self, other: list[float]) -> float:
        return sum(left * right for left, right in zip(self, other))


class B2EnhancedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "data"
        self.output_dir = self.root / "outputs"
        (self.data_root / "docs").mkdir(parents=True)
        (self.data_root / "docs" / "agent.txt").write_text(
            "Agent cache stores deterministic tool results.", encoding="utf-8"
        )
        (self.data_root / "profile.json").write_text(
            json.dumps({"name": "agent", "active": True}), encoding="utf-8"
        )
        (self.data_root / "scores.csv").write_text(
            "name,score\nalpha,9\nbeta,8\ngamma,7\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_skill_error(self, code: str, function: object, *args: object, **kwargs: object) -> None:
        with self.assertRaises(SkillError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_enhanced_skills_have_complete_public_contracts(self) -> None:
        for function in (list_directory, python_executor, web_fetcher, read_and_convert):
            with self.subTest(skill=function.__name__):
                signature = inspect.signature(function)
                hints = get_type_hints(function)
                self.assertIn("return", hints)
                self.assertTrue(all(name in hints for name in signature.parameters))
                documentation = inspect.getdoc(function) or ""
                self.assertIn("Args:", documentation)
                self.assertIn("Returns:", documentation)
                self.assertIn("Raises:", documentation)

    def test_file_reader_json_and_bounded_csv_preview(self) -> None:
        json_result = file_reader("profile.json", data_root=str(self.data_root))
        self.assertEqual(json.loads(json_result["content"])["name"], "agent")
        self.assertEqual(json_result["format"], "json")

        csv_result = file_reader("scores.csv", max_rows=2, data_root=str(self.data_root))
        preview = json.loads(csv_result["content"])
        self.assertEqual(len(preview["rows"]), 2)
        self.assertTrue(csv_result["truncated_rows"])
        self.assertTrue(csv_result["truncated"])

    def test_semantic_modes_require_an_existing_local_model(self) -> None:
        for mode in ("semantic", "hybrid"):
            with self.subTest(mode=mode):
                self.assert_skill_error(
                    "SEMANTIC_MODEL_UNAVAILABLE",
                    local_file_search,
                    "tool cache",
                    root_dir="docs",
                    mode=mode,
                    data_root=str(self.data_root),
                    embedding_model_path=str(self.root / "missing-model"),
                )
        keyword = local_file_search(
            "tool cache", root_dir="docs", mode="keyword", data_root=str(self.data_root)
        )
        self.assertEqual(keyword["mode"], "keyword")
        self.assertEqual(len(keyword["results"]), 1)

    def test_semantic_scoring_uses_asymmetric_query_and_document_encoders(self) -> None:
        class FakeEmbedding:
            def __init__(self) -> None:
                self.query_calls = 0
                self.document_calls = 0

            def encode_query(self, text: str, **kwargs: object) -> DotVector:
                self.query_calls += 1
                self.assert_text = text
                return DotVector([1.0, 0.0])

            def encode_document(self, texts: list[str], **kwargs: object) -> list[DotVector]:
                self.document_calls += 1
                self.assert_documents = texts
                return [DotVector([1.0, 0.0]), DotVector([0.0, 1.0])]

        model = FakeEmbedding()
        with patch("skills.local_file_search._load_semantic_model", return_value=model):
            scores = _semantic_scores("query", ["relevant", "other"], "unused")
        self.assertEqual(scores, [1.0, 0.0])
        self.assertEqual(model.query_calls, 1)
        self.assertEqual(model.document_calls, 1)

    def test_semantic_score_normalization_preserves_order(self) -> None:
        normalized = _normalize_scores([0.2, 0.5, 0.3])
        self.assertEqual(normalized[:2], [0.0, 1.0])
        self.assertAlmostEqual(normalized[2], 1.0 / 3.0)
        self.assertEqual(_normalize_scores([0.4, 0.4]), [1.0, 1.0])

    def test_retrieval_metrics_report_quality_and_cold_warm_latency(self) -> None:
        metrics = _retrieval_metrics(
            [
                {"split": "sample", "rank": 1, "result_count": 5, "latency_ms": 100.0},
                {"split": "sample", "rank": 2, "result_count": 5, "latency_ms": 10.0},
                {"split": "sample", "rank": None, "result_count": 0, "latency_ms": 20.0},
            ]
        )["overall"]
        self.assertAlmostEqual(metrics["recall_at_1"], 1.0 / 3.0, places=6)
        self.assertAlmostEqual(metrics["recall_at_3"], 2.0 / 3.0, places=6)
        self.assertEqual(metrics["first_query_latency_ms"], 100.0)
        self.assertEqual(metrics["warm_mean_latency_ms"], 15.0)
        self.assertEqual(metrics["p95_latency_ms"], 100.0)

    def test_list_directory_bounds_and_does_not_follow_symlinks(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        link = self.data_root / "docs" / "outside-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symbolic links are unavailable")

        output = list_directory(
            "docs", recursive=True, max_depth=3, data_root=str(self.data_root)
        )
        linked = next(item for item in output["entries"] if item["name"] == "outside-link")
        self.assertEqual(linked["type"], "symlink")
        self.assertFalse(any(item["name"] == "secret.txt" for item in output["entries"]))
        self.assert_skill_error(
            "PATH_OUTSIDE_DATA_ROOT",
            list_directory,
            "../outside",
            data_root=str(self.data_root),
        )

    def test_read_and_convert_composes_existing_skills(self) -> None:
        output = read_and_convert(
            "docs/agent.txt",
            "markdown",
            "converted.md",
            data_root=str(self.data_root),
            output_dir=str(self.output_dir),
        )
        self.assertEqual(output["source"], "docs/agent.txt")
        self.assertTrue(Path(output["generated_file_path"]).is_file())
        self.assertIn("- Agent cache", output["formatted_text"])

    def test_python_executor_runs_calculation_in_child_process(self) -> None:
        output = python_executor("result = sorted([4, 1, 3, 2])")
        self.assertEqual(output["result"], [1, 2, 3, 4])
        self.assertTrue(output["isolation"]["child_process"])
        self.assertTrue(output["isolation"]["isolated_mode"])

    def test_python_executor_rejects_high_risk_capabilities(self) -> None:
        cases = [
            ("PYTHON_IMPORT_DENIED", "import os\nresult = os.listdir('/')"),
            ("PYTHON_IMPORT_DENIED", "import subprocess\nresult = subprocess.run(['id'])"),
            ("PYTHON_CAPABILITY_DENIED", "result = open('/etc/passwd').read()"),
            ("PYTHON_DYNAMIC_EXEC_DENIED", "result = eval('2 + 2')"),
            ("PYTHON_CAPABILITY_DENIED", "result = (1).__class__"),
        ]
        for code, source in cases:
            with self.subTest(code=code, source=source):
                self.assert_skill_error(code, python_executor, source)

    def test_python_executor_enforces_timeout_and_output_limit(self) -> None:
        self.assert_skill_error(
            "PYTHON_TIMEOUT", python_executor, "while True:\n    pass", timeout_seconds=0.2
        )
        self.assert_skill_error(
            "OUTPUT_LIMIT_EXCEEDED",
            python_executor,
            "result = 'x' * 100000",
            max_output_chars=1000,
        )

    def test_web_fetcher_rejects_unsafe_urls_before_request(self) -> None:
        cases = [
            ("URL_SCHEME_DENIED", "file:///etc/passwd", {}),
            ("URL_PRIVATE_ADDRESS", "http://127.0.0.1:8080", {"allow_http": True}),
            ("URL_PRIVATE_ADDRESS", "http://169.254.169.254/latest", {"allow_http": True}),
            ("URL_PRIVATE_ADDRESS", "http://localhost:8000", {"allow_http": True}),
            (
                "URL_CREDENTIALS_DENIED",
                "https://user:password@example.com/private",
                {"allowed_domains": ["example.com"]},
            ),
            ("URL_DOMAIN_DENIED", "https://example.org", {"allowed_domains": ["example.com"]}),
        ]
        for code, url, options in cases:
            with self.subTest(code=code, url=url):
                self.assert_skill_error(code, web_fetcher, url, **options)

    def test_b2_runner_injects_configured_local_model_path(self) -> None:
        captured: list[str | None] = []

        class FakeEmbedding:
            def encode_query(self, text: str, **kwargs: object) -> DotVector:
                return DotVector([1.0, 0.0])

            def encode_document(self, texts: list[str], **kwargs: object) -> list[DotVector]:
                return [DotVector([1.0, 0.0]) for _ in texts]

        def load_model(path: str | None) -> FakeEmbedding:
            captured.append(path)
            return FakeEmbedding()

        with patch("skills.local_file_search._load_semantic_model", side_effect=load_model):
            result = run_skill(
                "local_file_search",
                {"query": "cache", "root_dir": "docs", "mode": "semantic"},
                data_root=str(self.data_root),
                output_dir=str(self.output_dir),
                tools_config=str(PROJECT_ROOT / "configs" / "tools.yaml"),
            )
        expected = (PROJECT_ROOT.parent / "Qwen3-Embedding-0.6B").resolve()
        self.assertEqual(result["status"], "success")
        self.assertEqual(captured, [str(expected)])

    def test_b2_runner_enforces_configured_feature_switch(self) -> None:
        config = yaml.safe_load(
            (PROJECT_ROOT / "configs" / "tools.yaml").read_text(encoding="utf-8")
        )
        config["settings"]["features"]["semantic_search"] = False
        config_path = self.root / "tools-disabled.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        result = run_skill(
            "local_file_search",
            {"query": "cache", "root_dir": "docs", "mode": "semantic"},
            data_root=str(self.data_root),
            tools_config=str(config_path),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "FEATURE_DISABLED")


if __name__ == "__main__":
    unittest.main()

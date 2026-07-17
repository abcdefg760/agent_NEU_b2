from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import get_type_hints


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
for entry in (str(PROJECT_ROOT), str(CODE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from b2_run_skill import run_skill
from skills.calculator import calculator
from skills.file_reader import file_reader
from skills.format_converter import format_converter
from skills.local_file_search import local_file_search
from skills.table_analyzer import table_analyzer


class B2ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "data"
        self.output_dir = self.root / "outputs"
        (self.data_root / "docs").mkdir(parents=True)
        (self.data_root / "docs" / "intro.txt").write_text(
            "Agent tools provide bounded local capabilities.", encoding="utf-8"
        )
        (self.data_root / "docs" / "schema.md").write_text(
            "Tool schema describes arguments. Tool schema supports validation.", encoding="utf-8"
        )
        (self.data_root / "table.csv").write_text(
            "name,value\nalpha,2\nbeta,4\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_skills_have_annotations_and_google_sections(self) -> None:
        for function in (calculator, file_reader, local_file_search, table_analyzer, format_converter):
            with self.subTest(skill=function.__name__):
                hints = get_type_hints(function)
                signature = inspect.signature(function)
                self.assertIn("return", hints)
                self.assertTrue(all(name in hints for name in signature.parameters))
                documentation = inspect.getdoc(function) or ""
                self.assertIn("Args:", documentation)
                self.assertIn("Returns:", documentation)
                self.assertIn("Raises:", documentation)

    def test_calculator_success_and_structured_error(self) -> None:
        self.assertEqual(calculator("2 + 3 * 4"), {"result": 14})
        result = run_skill("calculator", {"expression": "1 / 0"})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "DIVISION_BY_ZERO")
        self.assertEqual(result["error"]["type"], "SkillError")
        self.assertIn("message", result["error"])
        self.assertFalse(result["error"]["retryable"])

    def test_file_reader_success_missing_and_escape(self) -> None:
        output = file_reader("docs/intro.txt", max_chars=12, data_root=str(self.data_root))
        self.assertEqual(output["content"], "Agent tools ")
        self.assertTrue(output["truncated"])

        missing = run_skill(
            "file_reader", {"path": "docs/missing.txt"}, data_root=str(self.data_root)
        )
        self.assertEqual(missing["error"]["code"], "FILE_NOT_FOUND")

        escaped = run_skill("file_reader", {"path": "../secret.txt"}, data_root=str(self.data_root))
        self.assertEqual(escaped["error"]["code"], "PATH_OUTSIDE_DATA_ROOT")

    def test_keyword_search_success_and_validation_error(self) -> None:
        output = local_file_search(
            "tool schema", root_dir="docs", top_k=2, data_root=str(self.data_root)
        )
        self.assertEqual(output["results"][0]["path"], "docs/schema.md")
        self.assertGreater(output["results"][0]["keyword_score"], 0)
        self.assertIsNone(output["results"][0]["semantic_score"])

        result = run_skill(
            "local_file_search",
            {"query": "tool", "file_types": ["pdf"]},
            data_root=str(self.data_root),
        )
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_FORMAT")

    def test_table_analyzer_success_and_format_error(self) -> None:
        output = table_analyzer("table.csv", data_root=str(self.data_root))
        self.assertEqual(output["num_rows"], 2)
        self.assertEqual(output["describe"]["value"]["mean"], 3.0)

        result = run_skill(
            "table_analyzer", {"path": "docs/intro.txt"}, data_root=str(self.data_root)
        )
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_FORMAT")

    def test_format_converter_success_error_and_filename_sanitization(self) -> None:
        output = format_converter(
            "name: agent\nstatus: ready",
            "json",
            "../../result.json",
            str(self.output_dir),
        )
        generated = Path(output["generated_file_path"])
        self.assertEqual(generated.parent, self.output_dir)
        self.assertEqual(generated.name, "result.json")
        self.assertEqual(json.loads(output["formatted_text"])["status"], "ready")

        result = run_skill(
            "format_converter",
            {"text": "item", "target_format": "xml"},
            output_dir=str(self.output_dir),
        )
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_FORMAT")


if __name__ == "__main__":
    unittest.main()

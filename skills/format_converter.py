from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from skills.errors import SkillError


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "outputs" / "format_converter_files"
DEFAULT_FILENAMES = {"markdown": "converted.md", "json": "converted.json"}
SUFFIXES = {"markdown": ".md", "json": ".json"}


def _parse_key_value_lines(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        if ":" not in line:
            raise SkillError(
                "CONVERSION_PARSE_ERROR",
                f"expected 'key: value' line: {line}",
                category="validation",
            )
        key, value = (part.strip() for part in line.split(":", 1))
        if not key or key in result:
            raise SkillError(
                "CONVERSION_PARSE_ERROR",
                f"invalid or duplicate key: {key}",
                category="validation",
            )
        result[key] = value
    if not result:
        raise SkillError(
            "CONVERSION_PARSE_ERROR",
            "text contains no convertible content",
            category="validation",
        )
    return result


def _safe_output_path(output_dir: str | None, output_filename: str | None, target_format: str) -> Path:
    directory = Path(output_dir).resolve() if output_dir else DEFAULT_OUTPUT_DIR.resolve()
    raw_name = output_filename.strip() if isinstance(output_filename, str) and output_filename.strip() else DEFAULT_FILENAMES[target_format]
    name = Path(raw_name).name
    suffix = SUFFIXES[target_format]
    path = Path(name)
    stem = path.stem or Path(DEFAULT_FILENAMES[target_format]).stem
    candidate = directory / f"{stem}{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}({index}){suffix}"
        index += 1
    return candidate


def _write_output_file(text: str, output_dir: str | None, output_filename: str | None, target_format: str) -> Path:
    target = _safe_output_path(output_dir, output_filename, target_format)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def format_converter(
    text: str,
    target_format: Literal["markdown", "json"],
    output_filename: str | None = None,
    output_dir: str | None = None,
) -> dict[str, str]:
    """Convert bounded text to Markdown or JSON and write one output artifact.

    Args:
        text: Non-empty source text to convert.
        target_format: Output format, either ``markdown`` or ``json``.
        output_filename: Optional basename for the generated artifact.
        output_dir: Framework-injected directory for generated artifacts.

    Returns:
        A mapping containing converted text and the generated file path.

    Raises:
        SkillError: If input text, target format, filename, or conversion is invalid.
    """
    if not isinstance(text, str) or not text.strip():
        raise SkillError("INVALID_ARGUMENT", "text must be a non-empty string", category="validation")
    if len(text) > 100_000:
        raise SkillError(
            "INPUT_LIMIT_EXCEEDED",
            "text must not exceed 100000 characters",
            category="limit",
        )
    if output_filename is not None and not isinstance(output_filename, str):
        raise SkillError(
            "INVALID_ARGUMENT",
            "output_filename must be a string or null",
            category="validation",
        )
    target = target_format.strip().lower() if isinstance(target_format, str) else ""
    if target == "markdown":
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        formatted_text = "\n".join(f"- {line}" for line in lines)
    elif target == "json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _parse_key_value_lines(text)
        formatted_text = json.dumps(parsed, ensure_ascii=False, indent=2)
    else:
        raise SkillError(
            "UNSUPPORTED_FORMAT",
            "target_format must be markdown or json",
            category="validation",
        )
    generated_path = _write_output_file(formatted_text, output_dir, output_filename, target)
    return {"formatted_text": formatted_text, "generated_file_path": str(generated_path)}

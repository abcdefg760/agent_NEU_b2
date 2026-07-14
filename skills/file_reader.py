from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any

from skills import resolve_data_path
from skills.errors import SkillError


MAX_RETURN_CHARS = 100_000
DEFAULT_MAX_FILE_BYTES = 1_000_000
SUPPORTED_SUFFIXES = {".txt", ".md", ".json", ".csv", ".tsv"}


def _read_structured_preview(text: str, suffix: str, max_rows: int) -> tuple[str, int | None, bool]:
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SkillError(
                "INVALID_JSON",
                f"invalid JSON at line {exc.lineno}, column {exc.colno}",
                category="validation",
            ) from exc
        return json.dumps(payload, ensure_ascii=False, indent=2), None, False

    delimiter = "\t" if suffix == ".tsv" else ","
    reader = csv.DictReader(StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise SkillError("INVALID_TABLE", "table must contain a header row", category="validation")
    rows = []
    truncated = False
    for index, row in enumerate(reader):
        if index >= max_rows:
            truncated = True
            break
        rows.append(row)
    preview = {"columns": list(reader.fieldnames), "rows": rows}
    return json.dumps(preview, ensure_ascii=False, indent=2), len(rows), truncated


def file_reader(
    path: str,
    max_chars: int = 2000,
    max_rows: int = 100,
    *,
    data_root: str | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> dict[str, Any]:
    """Read a bounded UTF-8 text, JSON, CSV, or TSV file below the data root.

    Args:
        path: File path relative to the configured data root.
        max_chars: Maximum number of decoded characters returned.
        max_rows: Maximum number of CSV or TSV data rows returned.
        data_root: Framework-injected root that contains readable files.
        max_file_bytes: Framework-injected upper bound for source file size.

    Returns:
        A mapping with content, format, normalized source path, and truncation data.

    Raises:
        SkillError: If the path, format, encoding, or configured limits are invalid.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise SkillError(
            "INVALID_ARGUMENT",
            "max_chars must be a positive integer",
            category="validation",
        )
    if max_chars > MAX_RETURN_CHARS:
        raise SkillError(
            "INPUT_LIMIT_EXCEEDED",
            f"max_chars must not exceed {MAX_RETURN_CHARS}",
            category="limit",
            details={"max_chars": MAX_RETURN_CHARS},
        )
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows <= 0:
        raise SkillError(
            "INVALID_ARGUMENT",
            "max_rows must be a positive integer",
            category="validation",
        )
    if max_rows > 10_000:
        raise SkillError(
            "INPUT_LIMIT_EXCEEDED",
            "max_rows must not exceed 10000",
            category="limit",
        )
    if not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool) or max_file_bytes <= 0:
        raise SkillError(
            "CONFIGURATION_ERROR",
            "max_file_bytes must be a positive integer",
            category="configuration",
        )
    source, root = resolve_data_path(path, data_root)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise SkillError(
            "UNSUPPORTED_FORMAT",
            "file_reader supports .txt, .md, .json, .csv, and .tsv files",
            category="validation",
        )
    if not source.is_file():
        raise SkillError("FILE_NOT_FOUND", f"file not found: {path}", category="not_found")
    file_size = source.stat().st_size
    if file_size > max_file_bytes:
        raise SkillError(
            "FILE_SIZE_LIMIT_EXCEEDED",
            f"file exceeds {max_file_bytes} bytes",
            category="limit",
            details={"file_size": file_size, "max_file_bytes": max_file_bytes},
        )
    try:
        original = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError(
            "INVALID_TEXT_ENCODING",
            "file must be valid UTF-8 text",
            category="validation",
        ) from exc
    rendered, num_rows, truncated_rows = _read_structured_preview(original, suffix, max_rows) if suffix in {".json", ".csv", ".tsv"} else (original, None, False)
    content = rendered[:max_chars]
    return {
        "content": content,
        "num_chars": len(content),
        "source": source.relative_to(root).as_posix(),
        "truncated": len(rendered) > len(content) or truncated_rows,
        "format": suffix.lstrip("."),
        "num_rows": num_rows,
        "truncated_rows": truncated_rows,
    }

from __future__ import annotations

import csv
import statistics

from typing import Any

from skills import resolve_data_path
from skills.errors import SkillError


def table_analyzer(
    path: str,
    max_rows_preview: int = 5,
    describe: bool = True,
    *,
    data_root: str | None = None,
    max_file_bytes: int = 2_000_000,
    max_rows_limit: int = 10_000,
) -> dict[str, Any]:
    """Analyze a bounded CSV or TSV file below the configured data root.

    Args:
        path: Table path relative to the configured data root.
        max_rows_preview: Number of leading rows included in the preview.
        describe: Whether to calculate basic statistics for complete numeric columns.
        data_root: Framework-injected root that contains readable tables.
        max_file_bytes: Framework-injected upper bound for source file size.
        max_rows_limit: Framework-injected upper bound for parsed data rows.

    Returns:
        A mapping with shape, columns, preview rows, and numeric statistics.

    Raises:
        SkillError: If the table, arguments, encoding, or limits are invalid.
    """
    if not isinstance(max_rows_preview, int) or isinstance(max_rows_preview, bool) or max_rows_preview < 0:
        raise SkillError(
            "INVALID_ARGUMENT",
            "max_rows_preview must be a non-negative integer",
            category="validation",
        )
    if max_rows_preview > 100:
        raise SkillError(
            "INPUT_LIMIT_EXCEEDED",
            "max_rows_preview must not exceed 100",
            category="limit",
        )
    if not isinstance(describe, bool):
        raise SkillError("INVALID_ARGUMENT", "describe must be a boolean", category="validation")
    if not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool) or max_file_bytes <= 0:
        raise SkillError("CONFIGURATION_ERROR", "max_file_bytes must be positive", category="configuration")
    if not isinstance(max_rows_limit, int) or isinstance(max_rows_limit, bool) or max_rows_limit <= 0:
        raise SkillError(
            "CONFIGURATION_ERROR",
            "max_rows_limit must be positive",
            category="configuration",
        )
    source, root = resolve_data_path(path, data_root)
    if source.suffix.lower() not in {".csv", ".tsv"}:
        raise SkillError(
            "UNSUPPORTED_FORMAT",
            "table_analyzer only supports .csv and .tsv files",
            category="validation",
        )
    if not source.is_file():
        raise SkillError("FILE_NOT_FOUND", f"table file not found: {path}", category="not_found")
    if source.stat().st_size > max_file_bytes:
        raise SkillError(
            "FILE_SIZE_LIMIT_EXCEEDED",
            f"table exceeds {max_file_bytes} bytes",
            category="limit",
        )
    delimiter = "\t" if source.suffix.lower() == ".tsv" else ","
    try:
        with source.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if not reader.fieldnames:
                raise SkillError(
                    "INVALID_TABLE",
                    "table must contain a header row",
                    category="validation",
                )
            rows = []
            for row in reader:
                rows.append(row)
                if len(rows) > max_rows_limit:
                    raise SkillError(
                        "TABLE_ROW_LIMIT_EXCEEDED",
                        f"table contains more than {max_rows_limit} rows",
                        category="limit",
                    )
            columns = list(reader.fieldnames)
    except UnicodeDecodeError as exc:
        raise SkillError(
            "INVALID_TEXT_ENCODING",
            "table must be valid UTF-8 text",
            category="validation",
        ) from exc
    stats: dict[str, dict] = {}
    if describe:
        for column in columns:
            raw_values = [row.get(column, "").strip() for row in rows]
            if not raw_values or any(value == "" for value in raw_values):
                continue
            try:
                values = [float(value) for value in raw_values]
            except ValueError:
                continue
            stats[column] = {
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "mean": statistics.fmean(values),
            }
    return {
        "path": source.relative_to(root).as_posix(),
        "num_rows": len(rows),
        "num_columns": len(columns),
        "columns": columns,
        "preview": rows[:max_rows_preview],
        "describe": stats,
    }

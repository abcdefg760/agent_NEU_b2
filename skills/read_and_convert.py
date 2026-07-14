from __future__ import annotations

from typing import Any, Literal

from skills.file_reader import file_reader
from skills.format_converter import format_converter


def read_and_convert(
    path: str,
    target_format: Literal["markdown", "json"],
    output_filename: str | None = None,
    max_chars: int = 20_000,
    *,
    data_root: str | None = None,
    output_dir: str | None = None,
    max_file_bytes: int = 1_000_000,
) -> dict[str, Any]:
    """Read one bounded local file and convert its content into an artifact.

    Args:
        path: Source path relative to the configured data root.
        target_format: Output format, either markdown or json.
        output_filename: Optional basename for the converted artifact.
        max_chars: Maximum source characters passed to conversion.
        data_root: Framework-injected root that contains readable files.
        output_dir: Framework-injected directory for generated artifacts.
        max_file_bytes: Framework-injected source file size limit.

    Returns:
        A mapping containing the nested read result and conversion result.

    Raises:
        SkillError: If either the bounded read or conversion operation fails.
    """
    read_result = file_reader(
        path,
        max_chars=max_chars,
        data_root=data_root,
        max_file_bytes=max_file_bytes,
    )
    conversion_result = format_converter(
        read_result["content"],
        target_format,
        output_filename,
        output_dir,
    )
    return {
        "source": read_result["source"],
        "read": read_result,
        "conversion": conversion_result,
        "formatted_text": conversion_result["formatted_text"],
        "generated_file_path": conversion_result["generated_file_path"],
    }

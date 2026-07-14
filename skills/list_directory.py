from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skills import resolve_data_path
from skills.errors import SkillError


def list_directory(
    path: str = ".",
    recursive: bool = False,
    max_depth: int = 1,
    max_entries: int = 100,
    *,
    data_root: str | None = None,
) -> dict[str, Any]:
    """List bounded directory metadata without following symbolic links.

    Args:
        path: Directory path relative to the configured data root.
        recursive: Whether to descend into child directories.
        max_depth: Maximum recursive depth below the requested directory.
        max_entries: Maximum number of entries returned.
        data_root: Framework-injected root that contains listable directories.

    Returns:
        A mapping with normalized root path, entry metadata, and truncation state.

    Raises:
        SkillError: If the path, argument types, or configured limits are invalid.
    """
    if not isinstance(recursive, bool):
        raise SkillError("INVALID_ARGUMENT", "recursive must be a boolean", category="validation")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= 10:
        raise SkillError(
            "INVALID_ARGUMENT",
            "max_depth must be an integer between 0 and 10",
            category="validation",
        )
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= 1000:
        raise SkillError(
            "INVALID_ARGUMENT",
            "max_entries must be an integer between 1 and 1000",
            category="validation",
        )
    directory, root = resolve_data_path(path, data_root)
    if not directory.is_dir():
        raise SkillError(
            "DIRECTORY_NOT_FOUND",
            f"directory not found: {path}",
            category="not_found",
        )

    entries: list[dict[str, Any]] = []
    truncated = False

    def visit(current: Path, depth: int) -> None:
        nonlocal truncated
        try:
            children = sorted(os.scandir(current), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise SkillError(
                "DIRECTORY_READ_ERROR",
                f"cannot list directory: {current.relative_to(root).as_posix()}",
                category="execution",
            ) from exc
        for child in children:
            if len(entries) >= max_entries:
                truncated = True
                return
            child_path = Path(child.path)
            stat = child.stat(follow_symlinks=False)
            if child.is_symlink():
                entry_type = "symlink"
            elif child.is_dir(follow_symlinks=False):
                entry_type = "directory"
            else:
                entry_type = "file"
            entries.append(
                {
                    "name": child.name,
                    "path": child_path.relative_to(root).as_posix(),
                    "type": entry_type,
                    "size": stat.st_size if entry_type == "file" else None,
                    "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    "depth": depth,
                }
            )
            if recursive and entry_type == "directory" and depth < max_depth:
                visit(child_path, depth + 1)
                if truncated:
                    return

    visit(directory, 0)
    return {
        "path": directory.relative_to(root).as_posix() or ".",
        "entries": entries,
        "count": len(entries),
        "truncated": truncated,
    }

from __future__ import annotations

from pathlib import Path, PureWindowsPath

from skills.errors import SkillError


DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def resolve_data_path(path: str, data_root: str | None = None) -> tuple[Path, Path]:
    if not isinstance(path, str) or not path.strip():
        raise SkillError(
            "INVALID_ARGUMENT",
            "path must be a non-empty string",
            category="validation",
        )
    root = Path(data_root).resolve() if data_root else DEFAULT_DATA_ROOT.resolve()
    raw_path = path.strip()
    if PureWindowsPath(raw_path).is_absolute() and not Path(raw_path).is_absolute():
        raise SkillError(
            "PATH_OUTSIDE_DATA_ROOT",
            f"path escapes data root: {path}",
            category="security",
        )
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SkillError(
            "PATH_OUTSIDE_DATA_ROOT",
            f"path escapes data root: {path}",
            category="security",
        ) from exc
    return candidate, root

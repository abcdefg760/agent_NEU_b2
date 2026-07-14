from __future__ import annotations

from typing import Any


class SkillError(Exception):
    """Expected Skill failure with a stable machine-readable error contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: str = "execution",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.retryable = retryable
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """Return the extended error payload while preserving legacy fields."""
        payload: dict[str, Any] = {
            "type": type(self).__name__,
            "message": str(self),
            "code": self.code,
            "category": self.category,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


def exception_to_error(exc: Exception) -> dict[str, Any]:
    """Convert arbitrary exceptions to the shared backward-compatible payload."""
    if isinstance(exc, SkillError):
        return exc.to_dict()
    if isinstance(exc, FileNotFoundError):
        code, category, retryable = "FILE_NOT_FOUND", "not_found", False
    elif isinstance(exc, PermissionError):
        code, category, retryable = "PERMISSION_DENIED", "security", False
    elif isinstance(exc, TimeoutError):
        code, category, retryable = "EXECUTION_TIMEOUT", "timeout", True
    elif isinstance(exc, (TypeError, ValueError)):
        code, category, retryable = "INVALID_ARGUMENT", "validation", False
    else:
        code, category, retryable = "SKILL_EXECUTION_ERROR", "execution", False
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "code": code,
        "category": category,
        "retryable": retryable,
    }

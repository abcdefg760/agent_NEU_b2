from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

from skills.errors import SkillError


ALLOWED_IMPORTS = frozenset({"math", "statistics"})
DENIED_DYNAMIC_CALLS = frozenset({"eval", "exec", "compile", "__import__"})
DENIED_CAPABILITY_CALLS = frozenset(
    {
        "open",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "help",
    }
)
MAX_CODE_CHARS = 10_000
MAX_TIMEOUT_SECONDS = 10.0


class _SafetyValidator(ast.NodeVisitor):
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".", 1)[0] not in ALLOWED_IMPORTS:
                raise SkillError(
                    "PYTHON_IMPORT_DENIED",
                    f"import is not allowed: {alias.name}",
                    category="security",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = (node.module or "").split(".", 1)[0]
        if node.level or module not in ALLOWED_IMPORTS:
            raise SkillError(
                "PYTHON_IMPORT_DENIED",
                f"import is not allowed: {node.module or '<relative>'}",
                category="security",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise SkillError(
                "PYTHON_CAPABILITY_DENIED",
                f"private or special attribute access is not allowed: {node.attr}",
                category="security",
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise SkillError(
                "PYTHON_CAPABILITY_DENIED",
                f"special name access is not allowed: {node.id}",
                category="security",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in DENIED_DYNAMIC_CALLS:
                raise SkillError(
                    "PYTHON_DYNAMIC_EXEC_DENIED",
                    f"dynamic execution is not allowed: {node.func.id}",
                    category="security",
                )
            if node.func.id in DENIED_CAPABILITY_CALLS:
                raise SkillError(
                    "PYTHON_CAPABILITY_DENIED",
                    f"capability is not allowed: {node.func.id}",
                    category="security",
                )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        raise SkillError(
            "PYTHON_CAPABILITY_DENIED",
            "class definitions are not allowed",
            category="security",
        )

    def visit_Global(self, node: ast.Global) -> None:
        raise SkillError(
            "PYTHON_CAPABILITY_DENIED",
            "global declarations are not allowed",
            category="security",
        )

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        raise SkillError(
            "PYTHON_CAPABILITY_DENIED",
            "nonlocal declarations are not allowed",
            category="security",
        )


_CHILD_RUNNER = r'''
import contextlib
import io
import json
import sys

payload = json.loads(sys.stdin.read())
limit = int(payload["max_output_chars"])
allowed_imports = frozenset(payload["allowed_imports"])
real_stdout = sys.stdout

class OutputLimitError(Exception):
    pass

class LimitedBuffer(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > limit:
            raise OutputLimitError("captured output exceeds configured limit")
        return super().write(value)

def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".", 1)[0] not in allowed_imports:
        raise ImportError("import is not allowed: " + name)
    return __import__(name, globals, locals, fromlist, level)

safe_builtins = {
    "__import__": safe_import,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "Exception": Exception,
    "filter": filter,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "TypeError": TypeError,
    "ValueError": ValueError,
    "zip": zip,
}
stdout_buffer = LimitedBuffer()
stderr_buffer = LimitedBuffer()
namespace = {"__builtins__": safe_builtins}
try:
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        exec(compile(payload["code"], "<controlled-python>", "exec"), namespace, namespace)
    result = namespace.get("result")
    try:
        encoded_result = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        encoded_result = json.dumps(repr(result), ensure_ascii=False)
    if len(encoded_result) > limit:
        raise OutputLimitError("result exceeds configured limit")
    response = {
        "status": "success",
        "stdout": stdout_buffer.getvalue(),
        "stderr": stderr_buffer.getvalue(),
        "result": json.loads(encoded_result),
    }
except OutputLimitError as exc:
    response = {"status": "error", "code": "OUTPUT_LIMIT_EXCEEDED", "message": str(exc)}
except BaseException as exc:
    response = {
        "status": "error",
        "code": "PYTHON_RUNTIME_ERROR",
        "message": type(exc).__name__ + ": " + str(exc),
    }
print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), file=real_stdout)
'''


def _validate_code(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SkillError(
            "PYTHON_SYNTAX_ERROR",
            f"invalid Python syntax at line {exc.lineno}",
            category="validation",
        ) from exc
    _SafetyValidator().visit(tree)


def _clean_environment(workdir: Path) -> dict[str, str]:
    environment = {
        "HOME": str(workdir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.defpath,
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "TMP": str(workdir),
        "TEMP": str(workdir),
        "TMPDIR": str(workdir),
    }
    return environment


def _linux_limit_callback(
    cpu_seconds: int,
    memory_limit_mb: int,
    max_output_chars: int,
) -> Any:
    def apply_limits() -> None:
        import resource

        memory_bytes = memory_limit_mb * 1024 * 1024
        file_bytes = max(4096, max_output_chars * 4)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))

    return apply_limits


def python_executor(
    code: str,
    timeout_seconds: float = 2.0,
    *,
    max_output_chars: int = 16_000,
    memory_limit_mb: int = 256,
) -> dict[str, Any]:
    """Execute restricted calculation code in an isolated child process.

    Args:
        code: Python source limited to computation and explicitly allowed imports.
        timeout_seconds: Wall-clock timeout for the isolated process.
        max_output_chars: Framework-injected bound for stdout, stderr, and result.
        memory_limit_mb: Framework-injected Linux address-space limit.

    Returns:
        A mapping with captured output, a JSON-compatible result, and latency.

    Raises:
        SkillError: If static policy, timeout, resource, or runtime checks fail.
    """
    if not isinstance(code, str) or not code.strip():
        raise SkillError("INVALID_ARGUMENT", "code must be a non-empty string", category="validation")
    if len(code) > MAX_CODE_CHARS:
        raise SkillError(
            "INPUT_LIMIT_EXCEEDED",
            f"code must not exceed {MAX_CODE_CHARS} characters",
            category="limit",
        )
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise SkillError(
            "INVALID_ARGUMENT",
            "timeout_seconds must be a number",
            category="validation",
        )
    timeout = float(timeout_seconds)
    if not 0.1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise SkillError(
            "INVALID_ARGUMENT",
            f"timeout_seconds must be between 0.1 and {MAX_TIMEOUT_SECONDS}",
            category="validation",
        )
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool) or not 100 <= max_output_chars <= 100_000:
        raise SkillError(
            "CONFIGURATION_ERROR",
            "max_output_chars must be between 100 and 100000",
            category="configuration",
        )
    if not isinstance(memory_limit_mb, int) or isinstance(memory_limit_mb, bool) or not 64 <= memory_limit_mb <= 1024:
        raise SkillError(
            "CONFIGURATION_ERROR",
            "memory_limit_mb must be between 64 and 1024",
            category="configuration",
        )
    _validate_code(code)

    payload = json.dumps(
        {
            "code": code,
            "max_output_chars": max_output_chars,
            "allowed_imports": sorted(ALLOWED_IMPORTS),
        }
    )
    started = perf_counter()
    with tempfile.TemporaryDirectory(prefix="agent-python-") as temporary:
        workdir = Path(temporary).resolve()
        command = [sys.executable, "-I", "-S", "-c", _CHILD_RUNNER]
        options: dict[str, Any] = {}
        if os.name == "posix":
            options["preexec_fn"] = _linux_limit_callback(
                max(1, int(timeout) + 1), memory_limit_mb, max_output_chars
            )
        try:
            completed = subprocess.run(
                command,
                input=payload,
                text=True,
                capture_output=True,
                cwd=workdir,
                env=_clean_environment(workdir),
                timeout=timeout,
                check=False,
                **options,
            )
        except subprocess.TimeoutExpired as exc:
            raise SkillError(
                "PYTHON_TIMEOUT",
                f"controlled Python exceeded {timeout} seconds",
                category="timeout",
            ) from exc
    latency_ms = round((perf_counter() - started) * 1000, 3)
    if completed.returncode != 0 or not completed.stdout.strip():
        raise SkillError(
            "PYTHON_RESOURCE_LIMIT",
            "controlled Python stopped because of a process or resource limit",
            category="limit",
            details={"returncode": completed.returncode},
        )
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SkillError(
            "PYTHON_PROTOCOL_ERROR",
            "controlled Python returned an invalid response",
            category="execution",
        ) from exc
    if response.get("status") != "success":
        raise SkillError(
            str(response.get("code") or "PYTHON_RUNTIME_ERROR"),
            str(response.get("message") or "controlled Python failed"),
            category="limit" if response.get("code") == "OUTPUT_LIMIT_EXCEEDED" else "execution",
        )
    return {
        "stdout": response.get("stdout", ""),
        "stderr": response.get("stderr", ""),
        "result": response.get("result"),
        "latency_ms": latency_ms,
        "isolation": {
            "child_process": True,
            "isolated_mode": True,
            "linux_resource_limits": os.name == "posix",
        },
    }

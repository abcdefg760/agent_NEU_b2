from __future__ import annotations

import ast
import math
import operator

from typing import Any

from skills.errors import SkillError


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise SkillError(
                "CALCULATION_LIMIT_EXCEEDED",
                "exponent magnitude must not exceed 12",
                category="limit",
            )
        try:
            result = _BINARY_OPERATORS[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise SkillError(
                "DIVISION_BY_ZERO",
                "division by zero",
                category="validation",
            ) from exc
        if isinstance(result, complex) or not math.isfinite(float(result)) or abs(result) > 1e100:
            raise SkillError(
                "CALCULATION_LIMIT_EXCEEDED",
                "calculation result is out of range",
                category="limit",
            )
        return result
    raise SkillError(
        "UNSUPPORTED_EXPRESSION",
        f"unsupported expression element: {type(node).__name__}",
        category="validation",
    )


def calculator(expression: str) -> dict[str, int | float]:
    """Evaluate a bounded arithmetic expression without using dynamic execution.

    Args:
        expression: Arithmetic expression containing numeric literals and supported
            unary or binary operators.

    Returns:
        A mapping containing the numeric calculation result.

    Raises:
        SkillError: If the expression is empty, malformed, unsupported, or exceeds
            configured arithmetic limits.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise SkillError(
            "INVALID_ARGUMENT",
            "expression must be a non-empty string",
            category="validation",
        )
    if len(expression) > 200:
        raise SkillError(
            "INPUT_LIMIT_EXCEEDED",
            "expression must not exceed 200 characters",
            category="limit",
            details={"max_chars": 200},
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise SkillError(
            "INVALID_EXPRESSION",
            "invalid arithmetic expression",
            category="validation",
        ) from exc
    return {"result": _evaluate(tree)}

"""Deterministic calculator local tool."""

from __future__ import annotations

import ast
import re
from decimal import Decimal, DecimalException, DivisionByZero, InvalidOperation, localcontext

from .local_tools import LocalToolError, LocalToolRegistry, LocalToolSpec

_MAX_EXPRESSION_CHARS = 256
_MAX_AST_NODES = 64
_MAX_POWER_ABS = 100
_NUMBER = re.compile(
    r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)

CALCULATOR_SPEC = LocalToolSpec(
    name="calculator",
    description=(
        "Evaluate exact decimal arithmetic and numeric comparisons. Use this whenever the "
        "answer needs a precise arithmetic result, magnitude comparison, ratio, or percentage "
        "calculation instead of doing mental arithmetic. Dotted version labels, dates, IP "
        "addresses, identifiers, and similar labels are not decimal numbers unless the user "
        "explicitly asks to treat them numerically."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": (
                    "A decimal arithmetic or comparison expression, for example "
                    "'3.9 > 3.11', '(17 * 23) + 5', or '15 / 100 * 80'."
                ),
                "maxLength": _MAX_EXPRESSION_CHARS,
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    },
)

CALCULATOR_POLICY = """[정확한 계산 도구]
정확한 산술 결과, 수의 크기 비교, 비율·백분율처럼 프로그램으로 검증 가능한 수치 계산이 현재
답변에 필요하면 암산으로 확정하지 말고 calculator를 사용하세요. 계산이 매우 간단해 보여도
동일합니다. calculator 결과는 해당 산술식의 기준으로 사용하고 결과와 모순되는 값을 만들지 마세요.

소수점이 들어간 문자열이라고 전부 숫자로 해석하지 마세요. 소프트웨어 버전, 날짜, IP 주소,
식별자처럼 점이 구분자인 표기는 문맥상 숫자 자체를 비교·계산하라는 요청이 아닌 한 calculator에
보내지 마세요. 도구가 오류를 반환하면 임의의 계산 결과를 지어내지 말고 식을 단순화해 다시
시도하거나 필요한 경우 계산 범위를 짧게 확인하세요.
"""


def _literal(expression: str, node: ast.Constant) -> Decimal:
    if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise LocalToolError("unsupported_expression")
    source = ast.get_source_segment(expression, node)
    if not isinstance(source, str):
        raise LocalToolError("invalid_number")
    token = source.replace("_", "")
    if not _NUMBER.fullmatch(token):
        raise LocalToolError("invalid_number")
    try:
        return Decimal(token)
    except InvalidOperation as exc:
        raise LocalToolError("invalid_number") from exc


def _require_decimal(value) -> Decimal:
    if not isinstance(value, Decimal):
        raise LocalToolError("unsupported_expression")
    return value


def _bounded_power(base: Decimal, exponent: Decimal) -> Decimal:
    integral = exponent.to_integral_value()
    if exponent != integral:
        raise LocalToolError("non_integer_power")
    power = int(integral)
    if abs(power) > _MAX_POWER_ABS:
        raise LocalToolError("power_too_large")
    return base**power


def _evaluate(expression: str, node):
    if isinstance(node, ast.Expression):
        return _evaluate(expression, node.body)
    if isinstance(node, ast.Constant):
        return _literal(expression, node)
    if isinstance(node, ast.UnaryOp):
        value = _require_decimal(_evaluate(expression, node.operand))
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
        raise LocalToolError("unsupported_expression")
    if isinstance(node, ast.BinOp):
        left = _require_decimal(_evaluate(expression, node.left))
        right = _require_decimal(_evaluate(expression, node.right))
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                return _bounded_power(left, right)
        except (DivisionByZero, InvalidOperation, DecimalException, ZeroDivisionError) as exc:
            raise LocalToolError("arithmetic_error") from exc
        raise LocalToolError("unsupported_expression")
    if isinstance(node, ast.Compare):
        left = _require_decimal(_evaluate(expression, node.left))
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            right = _require_decimal(_evaluate(expression, comparator))
            if isinstance(operator, ast.Eq):
                ok = left == right
            elif isinstance(operator, ast.NotEq):
                ok = left != right
            elif isinstance(operator, ast.Lt):
                ok = left < right
            elif isinstance(operator, ast.LtE):
                ok = left <= right
            elif isinstance(operator, ast.Gt):
                ok = left > right
            elif isinstance(operator, ast.GtE):
                ok = left >= right
            else:
                raise LocalToolError("unsupported_expression")
            if not ok:
                return False
            left = right
        return True
    raise LocalToolError("unsupported_expression")


def calculate(arguments: dict) -> dict:
    expression = arguments.get("expression")
    if not isinstance(expression, str):
        raise LocalToolError("invalid_expression")
    expression = expression.strip()
    if not expression or len(expression) > _MAX_EXPRESSION_CHARS:
        raise LocalToolError("invalid_expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise LocalToolError("invalid_expression") from exc
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise LocalToolError("expression_too_complex")

    try:
        with localcontext() as context:
            context.prec = 50
            value = _evaluate(expression, tree)
    except LocalToolError:
        raise
    except (DecimalException, OverflowError, ValueError) as exc:
        raise LocalToolError("arithmetic_error") from exc

    if isinstance(value, bool):
        return {
            "expression": expression,
            "type": "boolean",
            "value": value,
        }
    value = _require_decimal(value)
    if not value.is_finite():
        raise LocalToolError("non_finite_result")
    return {
        "expression": expression,
        "type": "number",
        "value": str(value),
    }


def register_calculator(registry: LocalToolRegistry) -> None:
    registry.register(CALCULATOR_SPEC, calculate)


__all__ = [
    "CALCULATOR_POLICY",
    "CALCULATOR_SPEC",
    "calculate",
    "register_calculator",
]

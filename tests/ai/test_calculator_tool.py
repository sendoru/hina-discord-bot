import pytest

from hina_bot.ai.calculator_tool import CALCULATOR_POLICY, CALCULATOR_SPEC, calculate
from hina_bot.ai.local_tools import LocalToolError


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("3.9 > 3.11", True),
        ("3.10 < 3.9", True),
        ("0.09 < 0.1", True),
        ("-3.11 > -3.9", True),
        ("1.000 == 1", True),
        ("3.11 < 3.9 < 4", True),
    ],
)
def test_calculator_compares_decimals_exactly(expression, expected):
    result = calculate({"expression": expression})

    assert result == {
        "expression": expression,
        "type": "boolean",
        "value": expected,
    }


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("0.1 + 0.2", "0.3"),
        ("17 * 23", "391"),
        ("(17 * 23) + 5", "396"),
        ("15 / 100 * 80", "12.00"),
        ("2 ** 10", "1024"),
        ("7 // 2", "3"),
        ("7 % 2", "1"),
    ],
)
def test_calculator_arithmetic_uses_decimal(expression, expected):
    result = calculate({"expression": expression})

    assert result == {
        "expression": expression,
        "type": "number",
        "value": expected,
    }


@pytest.mark.parametrize(
    ("expression", "code"),
    [
        ("", "invalid_expression"),
        ("1 / 0", "arithmetic_error"),
        ("2 ** 101", "power_too_large"),
        ("4 ** 0.5", "non_integer_power"),
        ("abs(-1)", "unsupported_expression"),
        ("__import__('os')", "unsupported_expression"),
        ("1 and 2", "unsupported_expression"),
        ("[1, 2]", "unsupported_expression"),
    ],
)
def test_calculator_rejects_unsafe_or_unsupported_expressions(expression, code):
    with pytest.raises(LocalToolError) as caught:
        calculate({"expression": expression})

    assert caught.value.code == code


def test_calculator_tool_description_leaves_label_semantics_to_model():
    assert CALCULATOR_SPEC.name == "calculator"
    assert "version" in CALCULATOR_SPEC.description.lower()
    assert "dates" in CALCULATOR_SPEC.description.lower()
    assert "IP" in CALCULATOR_SPEC.description
    assert "정확한 산술" in CALCULATOR_POLICY
    assert "소프트웨어 버전" in CALCULATOR_POLICY

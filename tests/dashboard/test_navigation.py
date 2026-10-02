import pytest

from hina_bot.dashboard.navigation import safe_return_to


@pytest.mark.parametrize("value", [
    "https://evil.example/traces", "//evil.example/traces", "/memory?q=x",
    "/traces/other", "/traces#fragment", "/traces?x=%0a", "/traces?x=%5c",
    "https://[invalid", "/traces%2f..%2fmemory",
])
def test_return_paths_are_allowlisted(value):
    assert safe_return_to(value, "/traces") == "/traces"


def test_return_path_preserves_filters_and_page():
    assert safe_return_to("/traces?q=hello&page=2", "/traces") == "/traces?q=hello&page=2"

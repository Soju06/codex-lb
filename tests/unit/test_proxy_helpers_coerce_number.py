from __future__ import annotations

import pytest

from app.core.types import JsonValue
from app.modules.proxy.helpers import _coerce_number

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (42, 42),
        (3.5, 3.5),
        (" 12.75 ", 12.75),
        ("1e3", 1000.0),
        ("-8.25", -8.25),
        (-7, -7),
    ],
)
def test_coerce_number_happy_path(value: JsonValue, expected: int | float) -> None:
    assert _coerce_number(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not-a-number",
        None,
        {},
        [],
    ],
)
def test_coerce_number_edge_cases_return_none(value: JsonValue) -> None:
    assert _coerce_number(value) is None

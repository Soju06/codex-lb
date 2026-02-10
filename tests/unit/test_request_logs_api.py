from __future__ import annotations

import pytest

from app.modules.request_logs.api import _parse_model_option

pytestmark = pytest.mark.unit


def test_parse_model_option_without_delimiter_uses_none_reasoning_effort():
    option = _parse_model_option("gpt-5")
    assert option is not None
    assert option.model == "gpt-5"
    assert option.reasoning_effort is None


def test_parse_model_option_with_blank_effort_uses_none_reasoning_effort():
    option = _parse_model_option("gpt-5:::")
    assert option is not None
    assert option.model == "gpt-5"
    assert option.reasoning_effort is None

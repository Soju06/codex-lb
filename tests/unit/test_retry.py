from __future__ import annotations

import pytest

from app.core.utils.retry import parse_retry_after

pytestmark = pytest.mark.unit


def test_parse_retry_after_seconds():
    assert parse_retry_after("Try again in 1.2s") == 1.2


def test_parse_retry_after_milliseconds():
    assert parse_retry_after("Try again in 500ms") == 0.5


def test_parse_retry_after_missing():
    assert parse_retry_after("no retry info") is None

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


def test_parse_retry_after_minutes():
    assert parse_retry_after("Try again in 20m") == 1200.0


def test_parse_retry_after_compound_minutes_seconds():
    assert parse_retry_after("Please try again in 6m0s.") == 360.0
    assert parse_retry_after("Try again in 1m30s") == 90.0


def test_parse_retry_after_compound_hours():
    assert parse_retry_after("Try again in 1h2m3s") == 3723.0


def test_parse_retry_after_word_units():
    assert parse_retry_after("Try again in 30 seconds") == 30.0
    assert parse_retry_after("Try again in 2 minutes") == 120.0


def test_parse_retry_after_rejects_unsupported_longer_unit():
    # A supported unit literal must not match when it only prefixes a longer,
    # unsupported word: "month" -> "m", "hippos" -> "h", "secondment" -> "sec".
    assert parse_retry_after("Try again in 1 month") is None
    assert parse_retry_after("Try again in 5 mo") is None
    assert parse_retry_after("Try again in 2 hippos") is None
    assert parse_retry_after("Try again in 3 secondment") is None


def test_parse_retry_after_unit_boundary_keeps_digit_runs():
    # The boundary forbids trailing letters only; digits still chain compound
    # components, so "6m0s" and friends stay intact.
    assert parse_retry_after("Try again in 6m0s") == 360.0
    assert parse_retry_after("Try again in 1h2m3s") == 3723.0

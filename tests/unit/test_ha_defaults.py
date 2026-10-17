"""Tests that verify production high-availability defaults are set correctly."""

from __future__ import annotations

import pytest

from app.modules.proxy.ring_membership import RING_STALE_THRESHOLD_SECONDS
from app.modules.proxy.work_admission import ADMISSION_WAIT_TIMEOUT_SECONDS

pytestmark = pytest.mark.unit


def test_ring_stale_threshold_is_30_seconds() -> None:
    assert RING_STALE_THRESHOLD_SECONDS == 30


def test_admission_wait_timeout_is_10_seconds() -> None:
    assert ADMISSION_WAIT_TIMEOUT_SECONDS == 10.0

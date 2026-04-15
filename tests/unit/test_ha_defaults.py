"""Tests that verify production high-availability defaults are set correctly."""

from __future__ import annotations

import pytest

from app.core.config.settings import Settings
from app.modules.proxy.ring_membership import RING_STALE_THRESHOLD_SECONDS

pytestmark = pytest.mark.unit


def test_session_bridge_response_create_concurrency_default_is_50() -> None:
    s = Settings()
    assert s.http_responses_session_bridge_response_create_concurrency == 50


def test_ring_stale_threshold_is_30_seconds() -> None:
    assert RING_STALE_THRESHOLD_SECONDS == 30


def test_admission_wait_timeout_default_is_10_seconds() -> None:
    s = Settings()
    assert s.proxy_admission_wait_timeout_seconds == 10.0

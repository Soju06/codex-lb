from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.auth.refresh import classify_refresh_error, should_refresh
from app.core.utils.time import utcnow

pytestmark = pytest.mark.unit


def test_should_refresh_after_interval():
    last = utcnow() - timedelta(days=9)
    assert should_refresh(last) is True


def test_should_refresh_within_interval():
    last = utcnow() - timedelta(days=1)
    assert should_refresh(last) is False


def test_classify_refresh_error_permanent():
    assert classify_refresh_error("refresh_token_expired") is True


def test_classify_refresh_error_temporary():
    assert classify_refresh_error("temporary_error") is False

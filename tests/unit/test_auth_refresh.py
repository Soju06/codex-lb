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
    assert classify_refresh_error("account_deactivated") is True


def test_classify_refresh_error_token_expired_is_permanent():
    # ``token_expired`` from the OAuth refresh endpoint means the refresh
    # request itself failed because the refresh token (or the session it
    # belonged to) is no longer usable. Treat it as a permanent failure so
    # the load balancer deactivates the account instead of looping retries.
    # Regression for #383.
    assert classify_refresh_error("token_expired") is True


def test_classify_refresh_error_temporary():
    assert classify_refresh_error("temporary_error") is False


# ── Per-account refresh jitter (Change A) ────────────────────────────────


def test_should_refresh_without_account_id_uses_unjittered_interval():
    """Legacy callers that omit ``account_id`` MUST observe the un-jittered
    interval. This keeps existing tests and any defensive fallback path
    behaving identically to the pre-jitter implementation.
    """
    last = utcnow() - timedelta(days=8, hours=12)
    assert should_refresh(last) is True
    last = utcnow() - timedelta(days=7, hours=12)
    assert should_refresh(last) is False


def test_jitter_offset_is_stable_per_account():
    from app.core.auth.refresh import _refresh_jitter_offset_seconds

    a = _refresh_jitter_offset_seconds("acc_target", jitter_hours=18.0)
    b = _refresh_jitter_offset_seconds("acc_target", jitter_hours=18.0)
    assert a == b


def test_jitter_offset_differs_across_accounts():
    """Two distinct account IDs MUST land in distinct slots of the
    window (probability of accidental collision is ~zero for SHA-256).
    """
    from app.core.auth.refresh import _refresh_jitter_offset_seconds

    a = _refresh_jitter_offset_seconds("acc_one", jitter_hours=18.0)
    b = _refresh_jitter_offset_seconds("acc_two", jitter_hours=18.0)
    assert a != b


def test_jitter_offset_is_bounded_by_window():
    """The signed offset MUST never escape ``[-jitter*3600, +jitter*3600]``."""
    from app.core.auth.refresh import _refresh_jitter_offset_seconds

    jitter_hours = 18.0
    bound = jitter_hours * 3600.0
    for i in range(200):
        offset = _refresh_jitter_offset_seconds(f"acc_{i}", jitter_hours=jitter_hours)
        assert -bound <= offset <= bound


def test_jitter_zero_window_disables_offset():
    """Operators that explicitly want the un-jittered interval back."""
    from app.core.auth.refresh import _refresh_jitter_offset_seconds

    assert _refresh_jitter_offset_seconds("acc", jitter_hours=0.0) == 0.0


def test_should_refresh_applies_account_jitter_to_threshold():
    """Two accounts at the same ``last_refresh`` MAY observe different
    decisions because their effective intervals differ.

    We run a small probe across many account ids at a ``last_refresh``
    that sits inside the jitter window (``interval - jitter`` …
    ``interval + jitter``). At least one account must return ``True``
    and at least one must return ``False`` — proving the threshold is
    actually shifting per account.
    """
    last = utcnow() - timedelta(days=8)
    decisions = {should_refresh(last, account_id=f"acc_{i}") for i in range(100)}
    # ``decisions`` should contain both ``True`` and ``False``.
    assert decisions == {True, False}

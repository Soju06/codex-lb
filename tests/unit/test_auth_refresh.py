from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.auth.refresh import (
    RefreshError,
    classify_refresh_error,
    is_refresh_claim_contention,
    is_refresh_persist_conflict,
    is_transient_refresh_contention,
    refresh_contention_kind,
    should_refresh,
)
from app.core.utils.time import utcnow

pytestmark = pytest.mark.unit


def _refresh_error(code: str, *, transport_error: bool = True, is_permanent: bool = False) -> RefreshError:
    return RefreshError(code, f"{code} message", is_permanent, transport_error=transport_error)


def test_claim_contention_predicate_is_narrow_to_claim_timeout():
    # BENIGN category (1): only ``refresh_claim_timeout`` -- a peer holds the
    # claim and THIS caller never exchanged the token.
    benign = _refresh_error("refresh_claim_timeout")
    assert is_refresh_claim_contention(benign) is True
    assert is_refresh_persist_conflict(benign) is False
    # The post-exchange CAS codes MUST NOT be classified as benign claim
    # contention any longer (the taxonomy split).
    for code in ("token_persist_conflict", "status_downgrade_conflict"):
        assert is_refresh_claim_contention(_refresh_error(code)) is False


def test_persist_conflict_predicate_covers_post_exchange_cas_codes():
    # POST-EXCHANGE category (2): the guarded-write CAS losses.
    for code in ("token_persist_conflict", "status_downgrade_conflict"):
        exc = _refresh_error(code)
        assert is_refresh_persist_conflict(exc) is True
        assert is_refresh_claim_contention(exc) is False


def test_transient_refresh_contention_predicate_is_the_union():
    # The failover/skip-penalty external gate treats BOTH categories the same.
    for code in ("refresh_claim_timeout", "token_persist_conflict", "status_downgrade_conflict"):
        assert is_transient_refresh_contention(_refresh_error(code)) is True


def test_genuine_transport_error_is_not_refresh_contention():
    # A GENUINE OAuth transport failure carries ``transport_error=True`` but is
    # the account/route's fault -- it MUST satisfy NONE of the predicates so it
    # keeps its normal health accounting.
    genuine = _refresh_error("transport_error")
    assert is_refresh_claim_contention(genuine) is False
    assert is_refresh_persist_conflict(genuine) is False
    assert is_transient_refresh_contention(genuine) is False
    assert refresh_contention_kind(genuine) is None


def test_non_transport_codes_are_never_contention():
    # ``transport_error=False`` must never be classified as contention even for a
    # matching code string.
    for code in ("refresh_claim_timeout", "token_persist_conflict", "status_downgrade_conflict"):
        exc = _refresh_error(code, transport_error=False)
        assert is_refresh_claim_contention(exc) is False
        assert is_refresh_persist_conflict(exc) is False
        assert is_transient_refresh_contention(exc) is False
        assert refresh_contention_kind(exc) is None


def test_refresh_contention_kind_labels_categories_distinctly():
    assert refresh_contention_kind(_refresh_error("refresh_claim_timeout")) == "claim_contention"
    assert refresh_contention_kind(_refresh_error("token_persist_conflict")) == "persist_conflict"
    assert refresh_contention_kind(_refresh_error("status_downgrade_conflict")) == "persist_conflict"


def test_should_refresh_after_interval():
    last = utcnow() - timedelta(days=9)
    assert should_refresh(last) is True


def test_should_refresh_within_interval():
    last = utcnow() - timedelta(days=1)
    assert should_refresh(last) is False


def test_classify_refresh_error_permanent():
    assert classify_refresh_error("refresh_token_expired") is True
    assert classify_refresh_error("account_deactivated") is True
    assert classify_refresh_error("invalid_grant") is True
    assert classify_refresh_error("app_session_terminated") is True


def test_classify_refresh_error_token_expired_is_permanent():
    # ``token_expired`` from the OAuth refresh endpoint means the refresh
    # request itself failed because the refresh token (or the session it
    # belonged to) is no longer usable. Treat it as a permanent failure so
    # the load balancer deactivates the account instead of looping retries.
    # Regression for #383.
    assert classify_refresh_error("token_expired") is True


def test_classify_refresh_error_temporary():
    assert classify_refresh_error("temporary_error") is False

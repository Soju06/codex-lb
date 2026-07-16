from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Dialect

from app.core.config.settings import Settings
from app.modules.accounts.refresh_claims import (
    build_refresh_claim_upsert,
    default_refresh_claimant_id,
)

pytestmark = pytest.mark.unit


def _compile(dialect_name: str, dialect: Dialect) -> str:
    stmt = build_refresh_claim_upsert(dialect_name=dialect_name)
    return str(stmt.compile(dialect=dialect))


def test_claim_upsert_compiles_for_postgresql() -> None:
    sql = _compile("postgresql", postgresql.dialect())
    assert "INSERT INTO account_refresh_claims" in sql
    assert "ON CONFLICT (account_id) DO UPDATE" in sql
    assert "claim_expires_at <" in sql
    assert "claimed_by =" in sql
    assert "RETURNING account_refresh_claims.account_id" in sql


def test_claim_upsert_uses_database_server_clock_not_bound_python_now() -> None:
    """Regression: claim TTL/expiry MUST be evaluated on the DB server clock so
    inter-replica wall-clock skew cannot steal a live claim. A skewed replica
    binding its local ``now`` would treat a peer's live claim as expired and
    re-exchange the same single-use refresh token."""
    pg = _compile("postgresql", postgresql.dialect())
    assert "clock_timestamp()" in pg
    assert "make_interval(secs =>" in pg
    # The takeover predicate compares against the server clock, never a client
    # bind parameter for the current instant.
    assert "claim_expires_at < now()" in pg

    lite = _compile("sqlite", sqlite.dialect())
    assert "strftime('%Y-%m-%d %H:%M:%f', 'now')" in lite
    assert "'now', '+' ||" in lite


def test_claim_upsert_rejects_unknown_dialect() -> None:
    with pytest.raises(RuntimeError):
        build_refresh_claim_upsert(dialect_name="mysql")


def test_default_claimant_id_includes_instance_id_and_fits_column() -> None:
    from app.core.config.settings import get_settings

    claimant_id = default_refresh_claimant_id()
    assert claimant_id.startswith(f"{get_settings().http_responses_session_bridge_instance_id}:")
    assert len(claimant_id) <= 128


def test_long_instance_id_truncates_base_and_preserves_process_and_owner_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a 128+ char instance id used to swallow the per-process
    suffix, collapsing all workers of one replica into a single claimant (the
    re-entrant claim upsert then granted the claim to several processes). The
    claimant id must also reserve room for the per-refresh owner suffix so the
    composed ``claimed_by`` fits the column without truncating either suffix."""
    from app.core.config.settings import get_settings
    from app.modules.accounts.refresh_claims import (
        _CLAIMANT_ID_MAX_LEN,
        _compose_claimed_by,
        _current_process_suffix,
    )

    process_suffix = _current_process_suffix()

    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "i" * 200)
    get_settings.cache_clear()
    try:
        claimant_id = default_refresh_claimant_id()
    finally:
        get_settings.cache_clear()

    assert len(claimant_id) == _CLAIMANT_ID_MAX_LEN
    assert claimant_id.endswith(f":{process_suffix}")
    # Composing with a full-length (64 hex) owner fingerprint still fits 128 and
    # preserves both the process suffix and the owner discriminator.
    composed = _compose_claimed_by(claimant_id, "f" * 64)
    assert len(composed) <= 128
    assert f":{process_suffix}" in composed


def test_compose_claimed_by_distinguishes_owners_and_preserves_owner_token() -> None:
    """Two owners on one claimant must yield distinct ``claimed_by`` values with
    the owner token preserved in full even when the claimant fills the column."""
    from app.modules.accounts.refresh_claims import _CLAIM_OWNER_TOKEN_LEN, _compose_claimed_by

    owner_a = "a" * 40
    owner_b = "b" * 40
    long_claimant = "c" * 200

    composed_a = _compose_claimed_by(long_claimant, owner_a)
    composed_b = _compose_claimed_by(long_claimant, owner_b)

    assert composed_a != composed_b
    assert len(composed_a) <= 128
    assert composed_a.endswith("a" * _CLAIM_OWNER_TOKEN_LEN)
    assert composed_b.endswith("b" * _CLAIM_OWNER_TOKEN_LEN)


def test_process_suffix_is_stable_within_one_process() -> None:
    """Genuine same-process re-entrancy relies on a stable claimant id: repeated
    calls in one process MUST return the same suffix (and thus the same
    claimant id), otherwise a crashed refresh could not reclaim its own live
    claim via the same-claimant re-entry predicate."""
    from app.modules.accounts.refresh_claims import _current_process_suffix

    assert _current_process_suffix() == _current_process_suffix()
    assert default_refresh_claimant_id() == default_refresh_claimant_id()


def test_forked_children_get_distinct_claimant_ids_after_preload() -> None:
    """Regression: the per-process suffix was frozen at module import, so in a
    pre-fork deployment (module preloaded in the parent before workers fork)
    every child inherited the SAME suffix. Two workers sharing one instance id
    then built the SAME ``claimed_by`` and both satisfied the re-entrant claim
    upsert (``claimed_by == claimed_by``), refreshing the single-use token
    concurrently. Two forked children (same instance id, module imported before
    the fork boundary) MUST therefore yield DISTINCT claimant ids and distinct
    composed ``claimed_by`` values."""
    import os

    from app.modules.accounts.refresh_claims import _compose_claimed_by

    # Resolve the claimant id in the parent BEFORE forking to model a preloaded
    # module: any lazily-cached suffix is populated with the parent's identity.
    parent_id = default_refresh_claimant_id()

    def _child_claimant_id() -> str:
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:  # pragma: no cover - runs in the forked child
            os.close(read_fd)
            try:
                os.write(write_fd, default_refresh_claimant_id().encode())
            finally:
                os.close(write_fd)
                os._exit(0)
        os.close(write_fd)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)
        assert status == 0
        return b"".join(chunks).decode()

    child_a = _child_claimant_id()
    child_b = _child_claimant_id()

    # Same instance id (same replica) but the per-process suffix must diverge.
    assert child_a != parent_id
    assert child_b != parent_id
    assert child_a != child_b

    owner = "f" * 64
    composed = {
        _compose_claimed_by(parent_id, owner),
        _compose_claimed_by(child_a, owner),
        _compose_claimed_by(child_b, owner),
    }
    assert len(composed) == 3


def test_process_default_coordinator_yields_distinct_claimants_across_fork() -> None:
    """Regression: the process-default coordinator froze its claimant id at
    construction. In a pre-fork deployment the coordinator is built during
    preload (``get_refresh_claim_coordinator()``) BEFORE the server forks its
    workers, so every forked child inherited the SAME coordinator instance with
    the SAME frozen ``claimant_id``. Siblings then composed identical
    ``claimed_by`` values and both satisfied the re-entrant claim upsert
    (``claimed_by == claimed_by``), refreshing the single-use token concurrently.

    Building the process-default coordinator before forking (modelling preload)
    and reading its claimant id from two forked children MUST yield DISTINCT
    claimant ids and distinct composed ``claimed_by`` for the same account+owner,
    while an explicitly injected claimant id stays untouched across the fork and
    repeated reads within one process are stable."""
    import os

    from app.modules.accounts.refresh_claims import (
        RefreshClaimCoordinator,
        _compose_claimed_by,
        get_refresh_claim_coordinator,
        reset_refresh_claim_coordinator,
    )

    reset_refresh_claim_coordinator()
    try:
        # Model preload: build the process-default coordinator in the parent
        # BEFORE forking, and resolve its id once so any lazy cache is warm with
        # the parent's identity.
        coordinator = get_refresh_claim_coordinator()
        assert isinstance(coordinator, RefreshClaimCoordinator)
        parent_id = coordinator.claimant_id
        # Same-process reads are stable (genuine re-entrancy still matches).
        assert coordinator.claimant_id == parent_id

        # An explicitly injected id is owned by the caller and must not drift,
        # neither across repeated reads nor across a fork.
        explicit = RefreshClaimCoordinator(claimant_id="pinned-claimant")
        assert explicit.claimant_id == "pinned-claimant"

        owner = "f" * 64

        def _child_ids() -> tuple[str, str]:
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:  # pragma: no cover - runs in the forked child
                os.close(read_fd)
                try:
                    payload = f"{coordinator.claimant_id}\n{explicit.claimant_id}"
                    os.write(write_fd, payload.encode())
                finally:
                    os.close(write_fd)
                    os._exit(0)
            os.close(write_fd)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
            os.close(read_fd)
            _, status = os.waitpid(pid, 0)
            assert status == 0
            default_id, explicit_id = b"".join(chunks).decode().split("\n")
            return default_id, explicit_id

        child_a_default, child_a_explicit = _child_ids()
        child_b_default, child_b_explicit = _child_ids()

        # The process-default coordinator's auto-derived id diverges per child.
        assert child_a_default != parent_id
        assert child_b_default != parent_id
        assert child_a_default != child_b_default

        composed = {
            _compose_claimed_by(parent_id, owner),
            _compose_claimed_by(child_a_default, owner),
            _compose_claimed_by(child_b_default, owner),
        }
        assert len(composed) == 3

        # The explicitly injected id is caller-owned and stable across the fork.
        assert child_a_explicit == "pinned-claimant"
        assert child_b_explicit == "pinned-claimant"
    finally:
        reset_refresh_claim_coordinator()


def test_claim_ttl_must_cover_admission_wait_plus_twice_the_refresh_timeout() -> None:
    # The claim is held across the refresh-admission wait AND the OAuth
    # exchange, so a TTL sized only around the HTTP timeout is rejected.
    with pytest.raises(ValidationError, match="token_refresh_claim_ttl_seconds"):
        Settings(
            token_refresh_timeout_seconds=8.0,
            proxy_admission_wait_timeout_seconds=10.0,
            token_refresh_claim_ttl_seconds=16.0,
        )
    settings = Settings(
        token_refresh_timeout_seconds=8.0,
        proxy_admission_wait_timeout_seconds=10.0,
        token_refresh_claim_ttl_seconds=26.0,
    )
    assert settings.token_refresh_claim_ttl_seconds == 26.0


def test_claim_ttl_default_derives_from_raised_timeouts_without_explicit_ttl() -> None:
    # A deployment that predates the claim-TTL setting may have raised the
    # refresh/admission timeouts without knowing to set the new field. That
    # config must still boot: the TTL default is derived from the related
    # timeouts (never below the invariant floor) instead of crashing against
    # the fixed 30s default.
    settings = Settings(
        token_refresh_timeout_seconds=11.0,
        proxy_admission_wait_timeout_seconds=14.0,
    )
    minimum_ttl = settings.proxy_admission_wait_timeout_seconds + 2.0 * settings.token_refresh_timeout_seconds
    assert settings.token_refresh_claim_ttl_seconds >= minimum_ttl
    assert settings.token_refresh_claim_ttl_seconds == minimum_ttl

    # A default deployment keeps the fixed 30s default (which already covers
    # the default timeout floor of 26s).
    default_settings = Settings()
    assert default_settings.token_refresh_claim_ttl_seconds == 30.0

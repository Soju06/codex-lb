"""Query-layer coverage for the thread identity and cache locality metrics."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, Base, RequestLog
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.service import ReportsService
from app.modules.reports.thread_identity import (
    CACHE_MIN_INPUT_TOKENS,
    CONVERSATION_MIN_REQUESTS,
    MAX_THREAD_IDENTITY_DAYS,
    SWITCH_MAX_GAP_SECONDS,
    ThreadIdentityFacetRow,
    aggregate_thread_identity,
)

pytestmark = pytest.mark.unit

WINDOW_DAY = date(2026, 6, 1)
WINDOW_START = datetime(2026, 6, 1, 0, 0)
WINDOW_END = datetime(2026, 6, 2, 0, 0)
BASE = datetime(2026, 6, 1, 12, 0)


@pytest.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(timezone.utc).replace(tzinfo=None),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _log(
    request_id: str,
    *,
    account_id: str | None = "acc-a",
    minutes: float = 0,
    conversation_id: str | None = None,
    session_id: str | None = None,
    api_key_id: str | None = "key-1",
    input_tokens: int | None = 10_000,
    cached_input_tokens: int | None = 9_000,
    status: str = "success",
    **extra: object,
) -> RequestLog:
    return RequestLog(
        account_id=account_id,
        api_key_id=api_key_id,
        request_id=request_id,
        conversation_id=conversation_id,
        session_id=session_id,
        requested_at=BASE + timedelta(minutes=minutes),
        model="gpt-5.1",
        status=status,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        **extra,
    )


async def _seed(session: AsyncSession, logs: list[RequestLog]) -> None:
    session.add_all([_account("acc-a"), _account("acc-b"), _account("acc-c")])
    session.add_all(logs)
    await session.commit()


async def _aggregate(session: AsyncSession) -> dict[bool, ThreadIdentityFacetRow]:
    return await aggregate_thread_identity(session, WINDOW_START, WINDOW_END)


async def test_conversation_and_session_keys_both_count_as_keyed(async_session: AsyncSession) -> None:
    await _seed(
        async_session,
        [
            _log("c1", conversation_id="conv-1", minutes=0),
            _log("c2", conversation_id="conv-1", minutes=1, account_id="acc-b"),
            _log("c3", conversation_id="conv-1", minutes=2),
            # A blank conversation id falls through to the session id.
            _log("s1", conversation_id="  ", session_id="sess-1", minutes=0),
            _log("s2", conversation_id=None, session_id="sess-1", minutes=1, account_id="acc-b"),
            _log("s3", session_id="sess-1", minutes=2, account_id="acc-c"),
            _log("u1", minutes=0),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[True].requests == 6
    assert facets[False].requests == 1
    # Two keyed conversations: conv-1 spans two accounts, sess-1 spans three.
    assert facets[True].conversations == 2
    assert facets[True].conversation_account_total == 5
    assert facets[True].single_account_conversations == 0


async def test_conversations_below_the_request_floor_are_excluded(async_session: AsyncSession) -> None:
    short = [_log(f"short-{i}", conversation_id="conv-short", minutes=i) for i in range(CONVERSATION_MIN_REQUESTS - 1)]
    long = [
        _log(f"long-{i}", conversation_id="conv-long", minutes=i, account_id="acc-a")
        for i in range(CONVERSATION_MIN_REQUESTS)
    ]
    await _seed(async_session, short + long)

    facets = await _aggregate(async_session)

    assert facets[True].conversations == 1
    assert facets[True].conversation_account_total == 1
    assert facets[True].single_account_conversations == 1


async def test_soft_deleted_rows_add_requests_but_no_account(async_session: AsyncSession) -> None:
    """Account deletion detaches ``account_id`` but the traffic still happened."""
    await _seed(
        async_session,
        [
            _log("k1", conversation_id="conv-1", minutes=0),
            _log("k2", conversation_id="conv-1", minutes=1),
            _log(
                "k3",
                conversation_id="conv-1",
                minutes=2,
                account_id=None,
                deleted_at=datetime(2026, 6, 5, 0, 0),
            ),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[True].requests == 3
    assert facets[True].unattributed_requests == 1
    assert facets[True].conversations == 1
    assert facets[True].conversation_account_total == 1
    assert facets[True].single_account_conversations == 1


async def test_internal_warmup_traffic_is_excluded(async_session: AsyncSession) -> None:
    await _seed(
        async_session,
        [
            _log("real", conversation_id="conv-1", minutes=0),
            _log("warm-kind", conversation_id="conv-1", minutes=1, request_kind="limit_warmup"),
            _log("warm-source", conversation_id="conv-1", minutes=2, source="limit_warmup"),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[True].requests == 1
    assert facets[True].conversations == 0


async def test_turn_qualification_gap_prefix_and_attribution(async_session: AsyncSession) -> None:
    gap_minutes = SWITCH_MAX_GAP_SECONDS / 60
    await _seed(
        async_session,
        [
            # Qualifying turn on a growing prefix, same account.
            _log("t1", conversation_id="conv-gap", minutes=0, input_tokens=10_000),
            _log("t2", conversation_id="conv-gap", minutes=1, input_tokens=11_000),
            # Beyond the gap: not a turn even though the prefix grows.
            _log("t3", conversation_id="conv-gap", minutes=1 + gap_minutes, input_tokens=12_000),
            # Shrinking prefix on a client-supplied conversation id. This is a
            # turn: Codex compacts a transcript mid-thread, which shrinks the
            # prefix without starting a new thread, and the conversation id is
            # the client's own thread identity either way.
            _log("p1", conversation_id="conv-prefix", minutes=0, input_tokens=20_000),
            _log("p2", conversation_id="conv-prefix", minutes=1, input_tokens=9_000, account_id="acc-b"),
            # An unattributed endpoint cannot prove a switch either way.
            _log("n1", conversation_id="conv-null", minutes=0),
            _log("n2", conversation_id="conv-null", minutes=1, account_id=None),
            _log("n3", conversation_id="conv-null", minutes=2, account_id="acc-b", input_tokens=11_000),
            # Usage never landed on m1 and m3 -- the shape of a failed request.
            # These still count: the thread id is the client's, and dropping
            # failure-adjacent pairs would blind the metric precisely where
            # switches happen, since a failure is what triggers the failover.
            _log("m1", conversation_id="conv-missing", minutes=0, input_tokens=None),
            _log("m2", conversation_id="conv-missing", minutes=1, input_tokens=10_000, account_id="acc-b"),
            _log("m3", conversation_id="conv-missing", minutes=2, input_tokens=None, account_id="acc-c"),
        ],
    )

    facets = await _aggregate(async_session)

    # t1->t2, p1->p2, m1->m2, m2->m3. Excluded: t2->t3 (beyond the gap) and both
    # conv-null pairs (an unattributed endpoint cannot prove a switch).
    assert facets[True].turns == 4
    assert facets[True].account_switches == 3


async def test_keyed_turns_adjacent_to_a_failure_are_not_dropped(async_session: AsyncSession) -> None:
    """A failed request records no usage; its neighbours must still count.

    Requiring a measured prefix on both endpoints of a keyed pair deleted every
    turn next to a failure, and a failure is the most common reason the next
    turn lands on a different account -- so the switch rate was biased down
    exactly where switches occur.
    """

    await _seed(
        async_session,
        [
            _log("f1", conversation_id="conv-fail", minutes=0, input_tokens=10_000),
            # The failed turn: no usage recorded.
            _log("f2", conversation_id="conv-fail", minutes=1, input_tokens=None, account_id="acc-b"),
            _log("f3", conversation_id="conv-fail", minutes=2, input_tokens=12_000, account_id="acc-c"),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[True].turns == 2
    assert facets[True].account_switches == 2


async def test_unkeyed_still_requires_a_non_shrinking_prefix(async_session: AsyncSession) -> None:
    """Unkeyed rows are grouped by API key, so the prefix is the only separator.

    Without a client thread id two unrelated threads on one API key would be
    stitched together, which would invent switches that never happened.
    """

    await _seed(
        async_session,
        [
            _log("u1", minutes=0, input_tokens=20_000),
            # Shrinking prefix with no thread id: a different thread, not a turn.
            _log("u2", minutes=1, input_tokens=9_000, account_id="acc-b"),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[False].turns == 0
    assert facets[False].account_switches == 0


async def test_switch_rate_counts_only_account_changes(async_session: AsyncSession) -> None:
    await _seed(
        async_session,
        [
            _log("s1", conversation_id="conv-1", minutes=0, input_tokens=10_000),
            _log("s2", conversation_id="conv-1", minutes=1, input_tokens=11_000, account_id="acc-b"),
            _log("s3", conversation_id="conv-1", minutes=2, input_tokens=12_000, account_id="acc-b"),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[True].turns == 2
    assert facets[True].account_switches == 1


async def test_a_conversation_and_session_id_sharing_a_value_stay_one_thread(
    async_session: AsyncSession,
) -> None:
    """The two columns share one identifier space in this deployment.

    Production carried the same value in both columns on 84% of the rows that
    had both, so qualifying the thread key by its source would split one real
    thread whenever a client stops sending the conversation id mid-thread.
    """
    await _seed(
        async_session,
        [
            _log("x1", conversation_id="thread-1", session_id="thread-1", minutes=0),
            _log("x2", conversation_id=None, session_id="thread-1", minutes=1, account_id="acc-b"),
            _log("x3", conversation_id="thread-1", session_id=None, minutes=2, account_id="acc-b"),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[True].conversations == 1
    assert facets[True].conversation_account_total == 2


async def test_unkeyed_turns_are_reconstructed_per_api_key(async_session: AsyncSession) -> None:
    await _seed(
        async_session,
        [
            _log("a1", api_key_id="key-a", minutes=0, input_tokens=10_000),
            _log("a2", api_key_id="key-a", minutes=1, input_tokens=11_000, account_id="acc-b"),
            _log("b1", api_key_id="key-b", minutes=2, input_tokens=12_000, account_id="acc-c"),
            # No API key at all: countable as a request, ungroupable as a thread.
            _log("orphan", api_key_id=None, minutes=3),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[False].requests == 4
    # key-a's two rows are one turn; key-b has a single row; the orphan is
    # excluded from thread grouping rather than merged with the other NULLs.
    assert facets[False].turns == 1
    assert facets[False].account_switches == 1


async def test_cache_ratio_samples_only_large_successful_requests(async_session: AsyncSession) -> None:
    await _seed(
        async_session,
        [
            _log("big", conversation_id="conv-1", input_tokens=20_000, cached_input_tokens=15_000, minutes=0),
            _log(
                "at-floor",
                conversation_id="conv-1",
                input_tokens=CACHE_MIN_INPUT_TOKENS,
                cached_input_tokens=CACHE_MIN_INPUT_TOKENS,
                minutes=1,
            ),
            _log(
                "errored",
                conversation_id="conv-1",
                input_tokens=30_000,
                cached_input_tokens=30_000,
                status="error",
                minutes=2,
            ),
            _log(
                "missing-usage",
                conversation_id="conv-1",
                input_tokens=None,
                cached_input_tokens=None,
                minutes=3,
            ),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[True].cache_input_tokens == 20_000
    assert facets[True].cache_cached_input_tokens == 15_000


async def test_rows_outside_the_window_are_never_scanned(async_session: AsyncSession) -> None:
    await _seed(
        async_session,
        [
            _log("inside", conversation_id="conv-1", minutes=0),
            _log("before", conversation_id="conv-1", minutes=-13 * 60),
            _log("after", conversation_id="conv-1", minutes=13 * 60),
        ],
    )

    facets = await _aggregate(async_session)

    assert facets[True].requests == 1


async def test_service_derives_ratios_and_marks_unkeyed_approximate(async_session: AsyncSession) -> None:
    await _seed(
        async_session,
        [
            _log("k1", conversation_id="conv-1", minutes=0, input_tokens=10_000, cached_input_tokens=9_000),
            _log("k2", conversation_id="conv-1", minutes=1, input_tokens=11_000, cached_input_tokens=9_000),
            _log(
                "k3",
                conversation_id="conv-1",
                minutes=2,
                account_id="acc-b",
                input_tokens=12_000,
                cached_input_tokens=0,
            ),
            _log("u1", minutes=0, input_tokens=10_000, cached_input_tokens=0),
        ],
    )
    service = ReportsService(ReportsRepository(async_session))

    response = await service.get_thread_identity(WINDOW_DAY, WINDOW_DAY, "UTC")

    assert response.available is True
    assert response.window_days == 1
    assert response.total_requests == 4
    assert response.unkeyed_request_share == 0.25
    assert response.keyed.mean_accounts_per_conversation == 2.0
    assert response.keyed.single_account_conversation_share == 0.0
    assert response.keyed.turns == 2
    assert response.keyed.account_switch_rate == 0.5
    assert response.keyed.cache_hit_ratio == round(18_000 / 33_000, 4)
    assert response.keyed.unattributed_request_share == 0.0
    assert response.keyed.thread_grouping_approximate is False
    assert response.unkeyed.thread_grouping_approximate is True
    assert response.unkeyed.cache_hit_ratio == 0.0


async def test_service_reports_unavailable_beyond_the_window_cap(async_session: AsyncSession) -> None:
    service = ReportsService(ReportsRepository(async_session))

    response = await service.get_thread_identity(
        WINDOW_DAY,
        WINDOW_DAY + timedelta(days=MAX_THREAD_IDENTITY_DAYS),
        "UTC",
    )

    assert response.available is False
    assert response.max_days == MAX_THREAD_IDENTITY_DAYS
    assert response.window_days == MAX_THREAD_IDENTITY_DAYS + 1
    assert response.total_requests == 0


async def test_service_honours_the_report_timezone(async_session: AsyncSession) -> None:
    """A local-midnight range maps to the same UTC bounds the reports use."""
    await _seed(
        async_session,
        [
            # 2026-06-01 12:00 UTC is 2026-06-01 21:00 in Asia/Seoul.
            _log("in-day", conversation_id="conv-1", minutes=0),
            # 2026-06-01 23:00 UTC is already 2026-06-02 in Asia/Seoul.
            _log("next-local-day", conversation_id="conv-1", minutes=11 * 60),
        ],
    )
    service = ReportsService(ReportsRepository(async_session))

    response = await service.get_thread_identity(WINDOW_DAY, WINDOW_DAY, "Asia/Seoul")

    assert response.total_requests == 1

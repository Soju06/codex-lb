"""Thread identity and cache locality metrics over the raw request-log window.

The panel these statements feed answers one operator question: how often is a
single thread of conversation served by more than one upstream account, and
what does the upstream prefix cache still return while that happens.

The figures are computed from raw ``request_logs`` rather than from the hourly
report rollup on purpose. The rollup folds away per-request ordering, and a
turn-to-turn account switch rate is defined on consecutive requests; it cannot
be reconstructed from folded buckets. Raw scans are the expensive path, so the
window is capped at :data:`MAX_THREAD_IDENTITY_DAYS` and every statement is
bounded by ``requested_at`` so PostgreSQL can range-scan
``idx_logs_requested_at_id`` instead of walking history.

Splitting the same figures by affinity source (``sticky_key_source`` /
``sticky_kind``, landed in #2352) needs only that column added to
:func:`_scoped` and to both grouping keys; nothing here reads it yet.

Row scope matches ``report_source``: internal warm-up traffic is excluded and
soft-deleted rows are kept, so the numbers line up with the rest of the Reports
page. Deleting an account detaches ``account_id`` from its history, and
``count(DISTINCT account_id)`` skips NULLs, so a detached row adds requests to a
thread without adding an account: a past window's accounts-per-conversation
factor drifts downward as accounts are deleted. That confounder is reported
rather than hidden — every facet also carries the share of its requests with no
attributed account, so a decayed factor is visible as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, Subquery, and_, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.logs import SUCCESS_STATUS
from app.db.models import RequestLog
from app.modules.accounts.usage_time_rollup import (
    _requested_at_epoch_bucket_expr,
    normalized_thread_key_expr,
)
from app.modules.reports.filters import _normal_traffic_clause

# Raw per-request scans cost roughly 100k rows per production day; seven days
# is the same ceiling the speed medians already use (MAX_SPEED_REPORT_DAYS).
MAX_THREAD_IDENTITY_DAYS = 7
# Conversations shorter than this are mostly one-shot probes whose account
# spread is uninformative; the baseline measurements used the same floor.
CONVERSATION_MIN_REQUESTS = 3
# Two requests further apart than this are treated as separate turns rather
# than one continued thread.
SWITCH_MAX_GAP_SECONDS = 600
# Below this prefix size a cache hit says little about prefix locality.
CACHE_MIN_INPUT_TOKENS = 5000


@dataclass(frozen=True, slots=True)
class ThreadIdentityFacetRow:
    """One keyed/unkeyed facet of the window, in raw counter form."""

    keyed: bool
    requests: int = 0
    unattributed_requests: int = 0
    cache_input_tokens: int = 0
    cache_cached_input_tokens: int = 0
    turns: int = 0
    account_switches: int = 0
    conversations: int = 0
    conversation_account_total: int = 0
    single_account_conversations: int = 0


def _scoped(session: AsyncSession, start_at: datetime, end_at: datetime) -> Subquery:
    """Window-bounded projection carrying the thread key and its facet."""
    conversation = normalized_thread_key_expr(RequestLog.conversation_id)
    session_key = normalized_thread_key_expr(RequestLog.session_id)
    # An integer flag rather than a boolean: SQLite has no native boolean and
    # grouping on the raw predicate would render differently per dialect.
    keyed = case((func.coalesce(conversation, session_key).is_not(None), 1), else_=0)
    # Unkeyed traffic has no thread at all, so it is reconstructed by API key —
    # the same stand-in the baseline analysis used. It is an approximation and
    # the response marks it as one.
    #
    # `conversation_id` and `session_id` share one identifier space here rather
    # than being two namespaces that could collide: over 2026-09-09..11,
    # 107,628 of the 128,260 rows carrying both held the *same* value, and only
    # 25 rows carried a conversation id without a session id. Qualifying the key
    # by its source would therefore split one real thread in two whenever a
    # client stops sending the conversation id mid-thread.
    thread_key = func.coalesce(conversation, session_key, RequestLog.api_key_id)
    return (
        select(
            keyed.label("keyed"),
            thread_key.label("thread_key"),
            RequestLog.account_id.label("account_id"),
            RequestLog.status.label("status"),
            # Left nullable on purpose: an unknown prefix size cannot prove a
            # turn continued, and NULL > 5000 correctly drops the row from the
            # cache sample.
            RequestLog.input_tokens.label("input_tokens"),
            func.coalesce(RequestLog.cached_input_tokens, 0).label("cached_input_tokens"),
            RequestLog.requested_at.label("requested_at"),
            # Whole-second epochs are enough for a ten-minute turn gap, and the
            # shared bucket helper keeps the dialect split in one place.
            _requested_at_epoch_bucket_expr(session, 1).label("requested_epoch"),
            RequestLog.id.label("id"),
        )
        .where(
            RequestLog.requested_at >= start_at,
            RequestLog.requested_at < end_at,
            _normal_traffic_clause(),
        )
        .subquery("thread_identity_scope")
    )


def _turns(scoped: Subquery) -> Subquery:
    """Each row paired with its predecessor in the same thread."""
    partition_by = [scoped.c.keyed, scoped.c.thread_key]
    order_by = [scoped.c.requested_at, scoped.c.id]
    return select(
        scoped.c.keyed,
        scoped.c.thread_key,
        scoped.c.account_id,
        scoped.c.status,
        scoped.c.input_tokens,
        scoped.c.cached_input_tokens,
        scoped.c.requested_epoch,
        func.lag(scoped.c.account_id).over(partition_by=partition_by, order_by=order_by).label("previous_account_id"),
        func.lag(scoped.c.input_tokens)
        .over(partition_by=partition_by, order_by=order_by)
        .label("previous_input_tokens"),
        func.lag(scoped.c.requested_epoch).over(partition_by=partition_by, order_by=order_by).label("previous_epoch"),
    ).subquery("thread_identity_turns")


def per_request_stmt(scoped: Subquery) -> Select:
    """Request volume, cache locality and turn-to-turn switches in one pass."""
    turns = _turns(scoped)
    # A turn needs both endpoints attributed to an account, a gap under the
    # threshold, and a measured non-shrinking prefix (a shrinking prefix is a
    # new thread reusing the key, not a continued one). Both prefix sizes must
    # be known: a request whose usage never landed cannot prove either way,
    # and treating its unknown size as zero would qualify the pair in one
    # direction and disqualify it in the other.
    # A shrinking prefix means the key was reused by a new thread, not that this
    # one continued -- but only where the key is a reconstruction. Both sizes
    # must be known for the comparison to mean anything.
    prefix_continues = and_(
        turns.c.input_tokens.is_not(None),
        turns.c.previous_input_tokens.is_not(None),
        turns.c.input_tokens >= turns.c.previous_input_tokens,
    )
    is_turn = and_(
        turns.c.thread_key.is_not(None),
        turns.c.previous_epoch.is_not(None),
        turns.c.account_id.is_not(None),
        turns.c.previous_account_id.is_not(None),
        turns.c.requested_epoch - turns.c.previous_epoch < SWITCH_MAX_GAP_SECONDS,
        # Keyed rows carry the client's own thread id, so the prefix heuristic
        # adds nothing there and costs a great deal: a failed request records no
        # usage, so requiring a measured prefix on BOTH endpoints deletes every
        # pair adjacent to a failure -- and a failure is the single most common
        # cause of the account switch this metric exists to count. Requiring it
        # would bias the switch rate downward exactly where switches happen.
        # Unkeyed rows are grouped by API key alone, so there the heuristic is
        # the only thing separating two threads and it stays mandatory.
        or_(turns.c.keyed == 1, prefix_continues),
    )
    is_cache_sample = and_(turns.c.status == SUCCESS_STATUS, turns.c.input_tokens > CACHE_MIN_INPUT_TOKENS)
    return select(
        turns.c.keyed,
        func.count().label("requests"),
        # Account deletion detaches history; surfacing the share keeps a
        # decayed accounts-per-conversation factor from reading as a real drop.
        func.coalesce(func.sum(case((turns.c.account_id.is_(None), 1), else_=0)), 0).label("unattributed_requests"),
        func.coalesce(func.sum(case((is_cache_sample, turns.c.input_tokens), else_=0)), 0).label("cache_input_tokens"),
        func.coalesce(func.sum(case((is_cache_sample, turns.c.cached_input_tokens), else_=0)), 0).label(
            "cache_cached_input_tokens"
        ),
        func.coalesce(func.sum(case((is_turn, 1), else_=0)), 0).label("turns"),
        func.coalesce(
            func.sum(case((and_(is_turn, turns.c.account_id != turns.c.previous_account_id), 1), else_=0)), 0
        ).label("account_switches"),
    ).group_by(turns.c.keyed)


def per_thread_stmt(scoped: Subquery) -> Select:
    """Account spread across the window's conversations."""
    threads = (
        select(
            scoped.c.keyed,
            scoped.c.thread_key,
            func.count().label("requests"),
            func.count(distinct(scoped.c.account_id)).label("accounts"),
        )
        .where(scoped.c.thread_key.is_not(None))
        .group_by(scoped.c.keyed, scoped.c.thread_key)
        .subquery("thread_identity_threads")
    )
    return (
        select(
            threads.c.keyed,
            func.count().label("conversations"),
            func.coalesce(func.sum(threads.c.accounts), 0).label("conversation_account_total"),
            func.coalesce(func.sum(case((threads.c.accounts == 1, 1), else_=0)), 0).label(
                "single_account_conversations"
            ),
        )
        .where(threads.c.requests >= CONVERSATION_MIN_REQUESTS)
        .group_by(threads.c.keyed)
    )


async def aggregate_thread_identity(
    session: AsyncSession,
    start_at: datetime,
    end_at: datetime,
) -> dict[bool, ThreadIdentityFacetRow]:
    """Both facets of the window, each as a raw counter row.

    Two bounded passes: one per request (window function over the thread
    partition) and one per thread (group by thread key). They cannot share a
    pass because ``count(DISTINCT account_id)`` per thread is not expressible
    as a window function.
    """
    scoped = _scoped(session, start_at, end_at)
    facets = {keyed: ThreadIdentityFacetRow(keyed=keyed) for keyed in (False, True)}
    for row in await session.execute(per_request_stmt(scoped)):
        keyed = bool(row.keyed)
        facets[keyed] = ThreadIdentityFacetRow(
            keyed=keyed,
            requests=int(row.requests or 0),
            unattributed_requests=int(row.unattributed_requests or 0),
            cache_input_tokens=int(row.cache_input_tokens or 0),
            cache_cached_input_tokens=int(row.cache_cached_input_tokens or 0),
            turns=int(row.turns or 0),
            account_switches=int(row.account_switches or 0),
        )
    for row in await session.execute(per_thread_stmt(scoped)):
        keyed = bool(row.keyed)
        current = facets[keyed]
        facets[keyed] = ThreadIdentityFacetRow(
            keyed=keyed,
            requests=current.requests,
            unattributed_requests=current.unattributed_requests,
            cache_input_tokens=current.cache_input_tokens,
            cache_cached_input_tokens=current.cache_cached_input_tokens,
            turns=current.turns,
            account_switches=current.account_switches,
            conversations=int(row.conversations or 0),
            conversation_account_total=int(row.conversation_account_total or 0),
            single_account_conversations=int(row.single_account_conversations or 0),
        )
    return facets

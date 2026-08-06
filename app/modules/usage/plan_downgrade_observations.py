"""Cross-replica evidence for workspace-less paid -> free plan downgrades.

A workspace-less usage payload that reports ``free`` for a paid account is not
trusted on a single sample: it is also the signature of a degraded or
wrong-identity usage response. The downgrade is applied only once two
consecutive refreshes agree (issue #1456).

Holding that evidence in process memory makes the sequence diverge whenever more
than one replica shares a database:

- replica A records ``free``, replica B observes a paid payload that should clear
  the evidence, then A observes ``free`` again and confirms a downgrade the
  cluster has already contradicted;
- conversely, two genuine ``free`` samples split across replicas each stall at
  one observation, so a real expiry never converges.

:class:`PlanDowngradeObservationStore` therefore keeps one row per account in
``account_plan_downgrade_observations``, so every replica reads and advances the
same count.

The stored ``credential_fingerprint`` pins evidence to the credential *lineage*
that produced it: a salted digest over the account's stable seat identity, never
over token material. Refresh tokens rotate on every successful token refresh
(``rotate_tokens`` in the accounts repository), and rotation extends the same
lineage rather than replacing it, so the digest is unmoved by rotation by
construction -- token bytes are not an input. Because nothing here decrypts, no
key rotation, re-encryption, or undecryptable row can perturb the digest either.

Replacing the credential is a different event from rotating it. Account ids are
deterministic (``generate_unique_account_id``) and ``upsert_account_slot``
updates the existing row in place, so a delete-and-re-import or an in-place
reauthentication reuses the same account id with *new* credentials -- and
evidence gathered under the previous credential must not count toward a
downgrade for the new one. Every such replacement flows through
``_apply_account_updates`` in the accounts repository, which discards this
account's pending evidence in the same transaction via
:func:`discard_plan_downgrade_observations`; deleting the account drops its row
through the schema's ``ondelete="CASCADE"``. The fingerprint's restart-at-one
comparison remains as defense in depth for any path that rebinds a row's seat
identity without passing through those seams.

Rows are deleted as soon as the downgrade is applied or the evidence is
invalidated.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import DateTime, String, bindparam, delete, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import Account, AccountPlanDowngradeObservation
from app.db.session import get_background_session, sqlite_writer_section

logger = logging.getLogger(__name__)

_TABLE_NAME = "account_plan_downgrade_observations"


def _is_missing_observations_schema(exc: Exception) -> bool:
    """True when the failure is only "this table has not been created yet".

    The observations table arrives with this change, so a replica running the new
    code against a not-yet-migrated database must degrade to process-local
    confirmation rather than fail every usage refresh: a stale plan label is a far
    smaller problem than a broken refresh loop. PostgreSQL and SQLite word the
    error differently, so both families are matched.
    """
    origin = getattr(exc, "orig", None)
    message = str(origin).lower() if origin is not None else str(exc).lower()
    return f"no such table: {_TABLE_NAME}" in message or f'relation "{_TABLE_NAME}" does not exist' in message


# Domain separation for the lineage digest. The fingerprint only ever needs to
# answer "is this the same credential lineage as last time?", so a fixed-salt
# digest is sufficient and keeps the value stable across replicas and restarts
# (a random per-process salt would make every replica disagree, reintroducing
# the very divergence this module removes). The salt is public by design: the
# digested fields are non-secret identity metadata, and the salt exists for
# domain separation, not secrecy. Bumped to v2 when the digest input changed
# from token material to seat identity, so values from the two schemes can
# never read as an agreeing lineage.
_FINGERPRINT_SALT = b"codex-lb/plan-downgrade-observation/v2"
_FINGERPRINT_LEN = 64


def credential_fingerprint(account: Account) -> str:
    """Return a stable, non-reversible fingerprint of an account's credential lineage.

    The digest is taken over the account's stable seat identity -- the ChatGPT
    workspace and principal identifiers, the email, and the sticky
    ``codex_installation_id`` -- and deliberately NOT over token material.
    Refresh tokens rotate on every successful token refresh, so a token-derived
    digest would read routine rotation as a credential replacement and restart
    pending downgrade evidence; an account whose token-refresh cadence
    interleaves with usage refresh would then never accumulate two agreeing
    observations and a real expiry could be postponed indefinitely (issue
    #1456). Seat identity survives rotation by construction, and because no
    input is encrypted there is no decrypt step to fail and no fallback path.

    Credential *replacement* (re-import or in-place reauthentication) does not
    move this digest either -- the same seat gets new tokens. That event resets
    pending evidence explicitly instead: every replacement flows through
    ``_apply_account_updates`` in the accounts repository, which calls
    :func:`discard_plan_downgrade_observations` in the same transaction.

    ``None`` and empty identity fields are encoded distinctly (JSON), so partial
    identities still compare equal only to themselves. Only equality ever
    matters here, never the preimage.
    """
    material = json.dumps(
        [
            account.chatgpt_account_id,
            account.chatgpt_user_id,
            account.email,
            account.codex_installation_id,
        ],
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(_FINGERPRINT_SALT, material, hashlib.sha256).hexdigest()
    return digest[:_FINGERPRINT_LEN]


# Logged at most once per process: an unmigrated database hits this on every
# credential replacement until the migration applies, and the first warning
# carries all the signal.
_discard_schema_missing_logged = False


async def discard_plan_downgrade_observations(session: AsyncSession, account_id: str) -> None:
    """Discard pending downgrade evidence inside the caller's transaction.

    Called by the accounts repository wherever fresh credential material is
    applied to an existing account row (re-import or in-place reauthentication):
    evidence gathered under the previous credential must not count toward a
    downgrade for the new one, so the new credential's first ``free`` payload
    can never land a downgrade on a single sample. Running inside the caller's
    transaction makes the discard atomic with the credential replacement.

    The ``DELETE`` runs in a SAVEPOINT so a database whose migration has not
    applied yet degrades to a warning instead of poisoning the caller's
    transaction (PostgreSQL aborts the whole transaction on a failed statement)
    and failing the import or reauthentication itself.
    """
    global _discard_schema_missing_logged
    try:
        async with session.begin_nested():
            await session.execute(
                delete(AccountPlanDowngradeObservation).where(AccountPlanDowngradeObservation.account_id == account_id)
            )
    except (OperationalError, ProgrammingError) as exc:
        if not _is_missing_observations_schema(exc):
            raise
        if not _discard_schema_missing_logged:
            _discard_schema_missing_logged = True
            logger.warning(
                "Plan-downgrade observation table is unavailable; credential replacement proceeds "
                "without discarding pending evidence until the database is migrated table=%s",
                _TABLE_NAME,
            )


@dataclass(frozen=True, slots=True)
class PlanDowngradeObservation:
    observations: int
    credential_fingerprint: str
    observed_plan_type: str


class PlanDowngradeObservationStorePort(Protocol):
    async def get(self, account_id: str) -> PlanDowngradeObservation | None: ...

    async def observe(
        self,
        account_id: str,
        *,
        credential_fingerprint: str,
        observed_plan_type: str,
    ) -> int: ...

    async def record(
        self,
        account_id: str,
        *,
        observations: int,
        credential_fingerprint: str,
        observed_plan_type: str,
    ) -> None: ...

    async def clear(self, account_id: str) -> None: ...


# Atomic observe: insert the first observation or increment an existing one in a
# single statement, so two concurrent refreshes for one account cannot both read
# the same prior count and lose an increment (a read-then-write pair has an await
# between the two halves and is not safe across replicas either way). The
# fingerprint check lives inside the same statement: a matching lineage
# increments, a changed lineage restarts at one. Mirrors the conditional-upsert
# approach in ``app/modules/accounts/refresh_claims.py``.
_OBSERVE_SQL_TEMPLATE = """
    INSERT INTO account_plan_downgrade_observations (
        account_id, observations, credential_fingerprint, observed_plan_type,
        first_observed_at, last_observed_at
    )
    VALUES (:account_id, 1, :fingerprint, :plan_type, :now, :now)
    ON CONFLICT (account_id) DO UPDATE SET
        observations = CASE
            WHEN {table}.credential_fingerprint = :fingerprint
             AND {table}.observed_plan_type = :plan_type
            THEN {table}.observations + 1
            ELSE 1
        END,
        credential_fingerprint = :fingerprint,
        observed_plan_type = :plan_type,
        first_observed_at = CASE
            WHEN {table}.credential_fingerprint = :fingerprint
             AND {table}.observed_plan_type = :plan_type
            THEN {table}.first_observed_at
            ELSE :now
        END,
        last_observed_at = :now
    RETURNING observations
"""


class PlanDowngradeObservationStore:
    """Database-backed store shared by every replica.

    When the table has not been migrated yet, every operation degrades to a
    process-local fallback so usage refresh keeps working; confirmation then loses
    only its cross-replica coherence, exactly the pre-change behavior.
    """

    def __init__(self, *, fallback: PlanDowngradeObservationStorePort | None = None) -> None:
        self._fallback = fallback if fallback is not None else InMemoryPlanDowngradeObservationStore()
        self._schema_missing = False

    def _degrade(self, exc: Exception) -> bool:
        if not _is_missing_observations_schema(exc):
            return False
        if not self._schema_missing:
            self._schema_missing = True
            logger.warning(
                "Plan-downgrade observation table is unavailable; falling back to process-local "
                "confirmation state until the database is migrated table=%s",
                _TABLE_NAME,
            )
        return True

    async def get(self, account_id: str) -> PlanDowngradeObservation | None:
        try:
            async with get_background_session() as session:
                row = await session.get(AccountPlanDowngradeObservation, account_id)
                if row is None:
                    return None
                return PlanDowngradeObservation(
                    observations=row.observations,
                    credential_fingerprint=row.credential_fingerprint,
                    observed_plan_type=row.observed_plan_type,
                )
        except (OperationalError, ProgrammingError) as exc:
            if not self._degrade(exc):
                raise
            return await self._fallback.get(account_id)

    async def observe(
        self,
        account_id: str,
        *,
        credential_fingerprint: str,
        observed_plan_type: str,
    ) -> int:
        """Atomically record an observation and return the resulting count.

        One statement decides between "increment" and "restart at one", so
        concurrent refreshes for the same account cannot lose an increment and a
        changed credential lineage still resets the sequence.
        """
        try:
            async with sqlite_writer_section():
                async with get_background_session() as session:
                    # Bind types are explicit so both drivers receive the same
                    # shapes: asyncpg in particular must see the naive UTC
                    # ``utcnow()`` values as a plain (timezone-less) TIMESTAMP
                    # rather than inferring a type for a textual parameter.
                    statement = text(_OBSERVE_SQL_TEMPLATE.format(table=_TABLE_NAME)).bindparams(
                        bindparam("account_id", type_=String()),
                        bindparam("fingerprint", type_=String()),
                        bindparam("plan_type", type_=String()),
                        bindparam("now", type_=DateTime()),
                    )
                    result = await session.execute(
                        statement,
                        {
                            "account_id": account_id,
                            "fingerprint": credential_fingerprint,
                            "plan_type": observed_plan_type,
                            "now": utcnow(),
                        },
                    )
                    observations = result.scalar_one()
                    await session.commit()
                    return int(observations)
        except (OperationalError, ProgrammingError) as exc:
            if not self._degrade(exc):
                raise
            return await self._fallback.observe(
                account_id,
                credential_fingerprint=credential_fingerprint,
                observed_plan_type=observed_plan_type,
            )

    async def record(
        self,
        account_id: str,
        *,
        observations: int,
        credential_fingerprint: str,
        observed_plan_type: str,
    ) -> None:
        now = utcnow()
        try:
            async with sqlite_writer_section():
                async with get_background_session() as session:
                    existing = await session.get(AccountPlanDowngradeObservation, account_id)
                    if existing is None:
                        session.add(
                            AccountPlanDowngradeObservation(
                                account_id=account_id,
                                observations=observations,
                                credential_fingerprint=credential_fingerprint,
                                observed_plan_type=observed_plan_type,
                                first_observed_at=now,
                                last_observed_at=now,
                            )
                        )
                    else:
                        existing.observations = observations
                        existing.credential_fingerprint = credential_fingerprint
                        existing.observed_plan_type = observed_plan_type
                        existing.last_observed_at = now
                    await session.commit()
        except (OperationalError, ProgrammingError) as exc:
            if not self._degrade(exc):
                raise
            await self._fallback.record(
                account_id,
                observations=observations,
                credential_fingerprint=credential_fingerprint,
                observed_plan_type=observed_plan_type,
            )

    async def clear(self, account_id: str) -> None:
        """Discard pending evidence, touching the writer path only when a row exists.

        Every workspace-less refresh that reports a paid plan clears here, and on
        a healthy account there is almost never a pending row -- so the common
        case is a cheap primary-key read with no writer lock, no ``DELETE``, and
        no ``COMMIT``. A row inserted concurrently after the read survives until
        the next paid observation, exactly as it would have survived an
        unconditional ``DELETE`` that committed before that insert: the gate
        changes the cost of the no-op case, not the semantics.
        """
        try:
            async with get_background_session() as session:
                existing = await session.get(AccountPlanDowngradeObservation, account_id)
            if existing is None:
                return
            async with sqlite_writer_section():
                async with get_background_session() as session:
                    await session.execute(
                        delete(AccountPlanDowngradeObservation).where(
                            AccountPlanDowngradeObservation.account_id == account_id
                        )
                    )
                    await session.commit()
        except (OperationalError, ProgrammingError) as exc:
            if not self._degrade(exc):
                raise
            await self._fallback.clear(account_id)

    async def account_ids(self) -> list[str]:
        """Every account with pending evidence (diagnostics and tests)."""
        async with get_background_session() as session:
            result = await session.execute(select(AccountPlanDowngradeObservation.account_id))
            return [row[0] for row in result.all()]


# Process-wide default store. ``_default_initialized`` distinguishes "not yet
# initialized" from an explicit override of ``None`` (persistence disabled --
# used by the test harness so DB-less unit tests keep exercising the guard
# against an in-memory store).
_default_store: PlanDowngradeObservationStorePort | None = None
_default_initialized: bool = False


def get_plan_downgrade_observation_store() -> PlanDowngradeObservationStorePort | None:
    global _default_store, _default_initialized
    if not _default_initialized:
        _default_store = PlanDowngradeObservationStore()
        _default_initialized = True
    return _default_store


def set_plan_downgrade_observation_store(store: PlanDowngradeObservationStorePort | None) -> None:
    """Override the process default (``None`` disables persistence)."""
    global _default_store, _default_initialized
    _default_store = store
    _default_initialized = True


def reset_plan_downgrade_observation_store() -> None:
    global _default_store, _default_initialized
    _default_store = None
    _default_initialized = False


class InMemoryPlanDowngradeObservationStore:
    """Process-local store used when no database is available.

    Preserves single-replica behavior for DB-less unit tests and for any
    deployment where the observations table has not been migrated yet; it cannot
    provide cross-replica coherence, which is why the database-backed store is
    the process default.
    """

    def __init__(self) -> None:
        self._rows: dict[str, PlanDowngradeObservation] = {}

    async def get(self, account_id: str) -> PlanDowngradeObservation | None:
        return self._rows.get(account_id)

    async def observe(
        self,
        account_id: str,
        *,
        credential_fingerprint: str,
        observed_plan_type: str,
    ) -> int:
        """Increment or restart the count without yielding control.

        No ``await`` between the read and the write, so this is atomic with
        respect to other tasks in this event loop — the in-process analogue of the
        database store's single-statement upsert.
        """
        existing = self._rows.get(account_id)
        if (
            existing is not None
            and existing.credential_fingerprint == credential_fingerprint
            and existing.observed_plan_type == observed_plan_type
        ):
            observations = existing.observations + 1
        else:
            observations = 1
        self._rows[account_id] = PlanDowngradeObservation(
            observations=observations,
            credential_fingerprint=credential_fingerprint,
            observed_plan_type=observed_plan_type,
        )
        return observations

    async def record(
        self,
        account_id: str,
        *,
        observations: int,
        credential_fingerprint: str,
        observed_plan_type: str,
    ) -> None:
        self._rows[account_id] = PlanDowngradeObservation(
            observations=observations,
            credential_fingerprint=credential_fingerprint,
            observed_plan_type=observed_plan_type,
        )

    async def clear(self, account_id: str) -> None:
        self._rows.pop(account_id, None)

    def clear_all(self) -> None:
        self._rows.clear()

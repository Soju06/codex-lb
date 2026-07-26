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

The stored ``credential_fingerprint`` pins evidence to the token material that
produced it. Account ids are deterministic (``generate_unique_account_id``) and
``upsert_account_slot`` updates the existing row in place, so a
delete-and-re-import or an in-place reauthentication reuses the same account id
with *new* credentials. Without the fingerprint the new credential would inherit
the previous one's pending observation and downgrade on its own first ``free``
payload -- exactly the single-sample trust this feature exists to prevent. The
fingerprint is a salted digest of the encrypted refresh-token material and never
stores or exposes token bytes.

Rows are deleted as soon as the downgrade is applied or the evidence is
invalidated, and the schema's ``ondelete="CASCADE"`` drops them with the account.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError, ProgrammingError

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


# Domain separation for the credential digest. The fingerprint only ever needs to
# answer "is this the same credential as last time?", so a fixed-salt digest is
# sufficient and keeps the value stable across replicas and restarts (a random
# per-process salt would make every replica disagree, reintroducing the very
# divergence this module removes).
_FINGERPRINT_SALT = b"codex-lb/plan-downgrade-observation/v1"
_FINGERPRINT_LEN = 64


def credential_fingerprint(account: Account) -> str:
    """Return a stable, non-reversible fingerprint of an account's credentials.

    Derived from the *encrypted* refresh-token material already held on the
    account row, so no plaintext token is read, and truncated to the stored
    column width. Only equality matters, never the preimage.
    """
    material = account.refresh_token_encrypted or b""
    if isinstance(material, memoryview):  # pragma: no cover - driver dependent
        material = material.tobytes()
    digest = hmac.new(_FINGERPRINT_SALT, bytes(material), hashlib.sha256).hexdigest()
    return digest[:_FINGERPRINT_LEN]


@dataclass(frozen=True, slots=True)
class PlanDowngradeObservation:
    observations: int
    credential_fingerprint: str
    observed_plan_type: str


class PlanDowngradeObservationStorePort(Protocol):
    async def get(self, account_id: str) -> PlanDowngradeObservation | None: ...

    async def record(
        self,
        account_id: str,
        *,
        observations: int,
        credential_fingerprint: str,
        observed_plan_type: str,
    ) -> None: ...

    async def clear(self, account_id: str) -> None: ...


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
        try:
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

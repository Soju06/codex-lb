from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import CursorResult, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.openai.model_registry import (
    ModelRegistryExport,
    ModelRegistrySnapshot,
    ReasoningLevel,
    UpstreamModel,
    get_model_registry,
)
from app.core.types import JsonValue
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import ModelRegistrySnapshotRecord
from app.db.session import get_background_session
from app.modules.proxy.account_cache import get_account_selection_cache

logger = logging.getLogger(__name__)

# Version of the persisted payload shape. MUST be bumped whenever the payload
# encoding changes (new/renamed fields, different container shapes); replicas
# ignore snapshots whose schema_version differs from their codec version.
SCHEMA_VERSION = 1

_SNAPSHOT_ROW_ID = 1

# Field-complete codec contracts: unit tests assert these sets equal the
# dataclass fields so a new field cannot ship without codec support.
UPSTREAM_MODEL_CODEC_FIELDS = frozenset(
    {
        "slug",
        "display_name",
        "description",
        "context_window",
        "input_modalities",
        "supported_reasoning_levels",
        "default_reasoning_level",
        "supports_reasoning_summaries",
        "support_verbosity",
        "default_verbosity",
        "prefer_websockets",
        "supports_parallel_tool_calls",
        "supported_in_api",
        "minimal_client_version",
        "priority",
        "available_in_plans",
        "base_instructions",
        "source_kind",
        "source_id",
        "raw",
    }
)

# ``fetched_at`` is monotonic-clock based and is persisted as the wall-clock
# ``refreshed_at`` column instead of appearing in the payload.
SNAPSHOT_CODEC_FIELDS = frozenset(
    {
        "models",
        "model_plans",
        "plan_models",
        "model_service_tier_plans",
        "model_service_tier_accounts",
        "account_plans",
        "fetched_at",
        "model_accounts",
        "account_catalogs_authoritative",
        "bootstrap_floor_active",
        "suppressed_model_slugs",
    }
)


@dataclass(slots=True)
class EncodedRegistrySnapshot:
    payload: str
    content_hash: str
    refreshed_at: datetime


@dataclass(slots=True)
class StoredSnapshotHeader:
    schema_version: int
    content_hash: str
    refreshed_at: datetime


def _encode_reasoning_level(level: ReasoningLevel) -> dict[str, JsonValue]:
    return {"effort": level.effort, "description": level.description}


def _encode_string_list(values: Iterable[str]) -> list[JsonValue]:
    """Re-type a string sequence as ``list[JsonValue]``.

    ``list`` is invariant, so e.g. ``sorted(...)``'s ``list[str]`` is not
    assignable to the ``list[JsonValue]`` arm of ``JsonValue``.
    """
    return [value for value in values]


def _decode_reasoning_level(data: Mapping[str, JsonValue]) -> ReasoningLevel:
    return ReasoningLevel(effort=str(data["effort"]), description=str(data["description"]))


def _encode_model(model: UpstreamModel) -> dict[str, JsonValue]:
    return {
        "slug": model.slug,
        "display_name": model.display_name,
        "description": model.description,
        "context_window": model.context_window,
        "input_modalities": list(model.input_modalities),
        "supported_reasoning_levels": [_encode_reasoning_level(level) for level in model.supported_reasoning_levels],
        "default_reasoning_level": model.default_reasoning_level,
        "supports_reasoning_summaries": model.supports_reasoning_summaries,
        "support_verbosity": model.support_verbosity,
        "default_verbosity": model.default_verbosity,
        "prefer_websockets": model.prefer_websockets,
        "supports_parallel_tool_calls": model.supports_parallel_tool_calls,
        "supported_in_api": model.supported_in_api,
        "minimal_client_version": model.minimal_client_version,
        "priority": model.priority,
        "available_in_plans": _encode_string_list(sorted(model.available_in_plans)),
        "base_instructions": model.base_instructions,
        "source_kind": model.source_kind,
        "source_id": model.source_id,
        "raw": model.raw,
    }


def _decode_model(data: Mapping[str, JsonValue]) -> UpstreamModel:
    reasoning_levels = data["supported_reasoning_levels"]
    input_modalities = data["input_modalities"]
    available_in_plans = data["available_in_plans"]
    raw = data["raw"]
    if (
        not isinstance(reasoning_levels, list)
        or not isinstance(input_modalities, list)
        or not isinstance(available_in_plans, list)
        or not isinstance(raw, dict)
    ):
        raise ValueError("Malformed persisted model entry")
    # Reject a wrong-typed reasoning level rather than dropping it, so a corrupt
    # entry surfaces as a decode failure instead of a silently-partial level set.
    decoded_levels: list[ReasoningLevel] = []
    for level in reasoning_levels:
        if not isinstance(level, dict):
            raise ValueError("Malformed persisted reasoning level")
        decoded_levels.append(_decode_reasoning_level(level))
    return UpstreamModel(
        slug=str(data["slug"]),
        display_name=str(data["display_name"]),
        description=str(data["description"]),
        context_window=int(_cast_int(data["context_window"])),
        input_modalities=tuple(str(item) for item in input_modalities),
        supported_reasoning_levels=tuple(decoded_levels),
        default_reasoning_level=_optional_str(data["default_reasoning_level"]),
        supports_reasoning_summaries=bool(data["supports_reasoning_summaries"]),
        support_verbosity=bool(data["support_verbosity"]),
        default_verbosity=_optional_str(data["default_verbosity"]),
        prefer_websockets=bool(data["prefer_websockets"]),
        supports_parallel_tool_calls=bool(data["supports_parallel_tool_calls"]),
        supported_in_api=bool(data["supported_in_api"]),
        minimal_client_version=_optional_str(data["minimal_client_version"]),
        priority=int(_cast_int(data["priority"])),
        available_in_plans=frozenset(str(item) for item in available_in_plans),
        base_instructions=str(data["base_instructions"]),
        source_kind=str(data["source_kind"]),
        source_id=_optional_str(data["source_id"]),
        raw=dict(raw),
    )


def _cast_int(value: JsonValue) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Expected numeric value, got {type(value).__name__}")
    return int(value)


def _optional_str(value: JsonValue) -> str | None:
    if value is None:
        return None
    return str(value)


def _encode_slug_sets(mapping: dict[str, frozenset[str]]) -> dict[str, JsonValue]:
    return {key: _encode_string_list(sorted(values)) for key, values in mapping.items()}


def _decode_slug_sets(data: JsonValue) -> dict[str, frozenset[str]]:
    if not isinstance(data, dict):
        raise ValueError("Malformed persisted set mapping")
    decoded: dict[str, frozenset[str]] = {}
    for key, values in data.items():
        # A wrong-typed value (e.g. a bare string where a list of slugs is
        # expected) MUST reject the whole decode rather than silently dropping
        # the entry: a dropped set-backed field would apply a partial catalog
        # (model present, plan gating gone) instead of surfacing corruption to
        # the poller. An empty list is a legitimately-empty set and is kept.
        if not isinstance(values, list):
            raise ValueError("Malformed persisted set mapping: expected a list of slugs")
        decoded[str(key)] = frozenset(str(item) for item in values)
    return decoded


def _encode_tier_sets(mapping: dict[str, dict[str, frozenset[str]]]) -> dict[str, JsonValue]:
    return {slug: _encode_slug_sets(tiers) for slug, tiers in mapping.items()}


def _decode_tier_sets(data: JsonValue) -> dict[str, dict[str, frozenset[str]]]:
    if not isinstance(data, dict):
        raise ValueError("Malformed persisted tier mapping")
    return {str(slug): _decode_slug_sets(tiers) for slug, tiers in data.items()}


def _encode_snapshot(snapshot: ModelRegistrySnapshot) -> dict[str, JsonValue]:
    return {
        "models": {slug: _encode_model(model) for slug, model in snapshot.models.items()},
        "model_plans": _encode_slug_sets(snapshot.model_plans),
        "plan_models": _encode_slug_sets(snapshot.plan_models),
        "model_service_tier_plans": _encode_tier_sets(snapshot.model_service_tier_plans),
        "model_service_tier_accounts": _encode_tier_sets(snapshot.model_service_tier_accounts),
        "account_plans": dict(snapshot.account_plans),
        "model_accounts": _encode_slug_sets(snapshot.model_accounts),
        "account_catalogs_authoritative": snapshot.account_catalogs_authoritative,
        "bootstrap_floor_active": snapshot.bootstrap_floor_active,
        "suppressed_model_slugs": _encode_string_list(sorted(snapshot.suppressed_model_slugs)),
    }


def _decode_models(data: JsonValue) -> dict[str, UpstreamModel]:
    if not isinstance(data, dict):
        raise ValueError("Malformed persisted models mapping")
    decoded: dict[str, UpstreamModel] = {}
    for slug, entry in data.items():
        # Reject a wrong-typed entry rather than dropping it, so a corrupt model
        # surfaces as a decode failure instead of a silently-partial catalog.
        if not isinstance(entry, dict):
            raise ValueError("Malformed persisted model entry")
        decoded[str(slug)] = _decode_model(entry)
    return decoded


def _decode_account_plans(data: JsonValue) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError("Malformed persisted account plans")
    return {str(key): str(value) for key, value in data.items()}


def _decode_snapshot(data: dict[str, JsonValue], *, fetched_at: float) -> ModelRegistrySnapshot:
    suppressed = data["suppressed_model_slugs"]
    if not isinstance(suppressed, list):
        raise ValueError("Malformed persisted suppression set")
    return ModelRegistrySnapshot(
        models=_decode_models(data["models"]),
        model_plans=_decode_slug_sets(data["model_plans"]),
        plan_models=_decode_slug_sets(data["plan_models"]),
        model_service_tier_plans=_decode_tier_sets(data["model_service_tier_plans"]),
        model_service_tier_accounts=_decode_tier_sets(data["model_service_tier_accounts"]),
        account_plans=_decode_account_plans(data["account_plans"]),
        fetched_at=fetched_at,
        model_accounts=_decode_slug_sets(data["model_accounts"]),
        account_catalogs_authoritative=bool(data["account_catalogs_authoritative"]),
        bootstrap_floor_active=bool(data["bootstrap_floor_active"]),
        suppressed_model_slugs=frozenset(str(item) for item in suppressed),
    )


def encode_registry_export(export: ModelRegistryExport) -> EncodedRegistrySnapshot:
    """Serialize registry state to a canonical JSON payload plus content hash.

    The payload deliberately excludes any timestamp so the content hash stays
    stable across refresh ticks that fetch an identical catalog; the wall-clock
    ``refreshed_at`` is carried as a separate column.
    """
    if export.snapshot is None:
        document: dict[str, JsonValue] = {"cleared": True, "snapshot": None, "metadata_models": None}
        refreshed_at = utcnow()
    else:
        document = {
            "cleared": False,
            "snapshot": _encode_snapshot(export.snapshot),
            "metadata_models": (
                {slug: _encode_model(model) for slug, model in export.metadata_models.items()}
                if export.metadata_models is not None
                else None
            ),
        }
        elapsed = max(0.0, time.monotonic() - export.snapshot.fetched_at)
        refreshed_at = utcnow() - timedelta(seconds=elapsed)
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return EncodedRegistrySnapshot(payload=payload, content_hash=content_hash, refreshed_at=refreshed_at)


def decode_registry_payload(payload: str, *, refreshed_at: datetime) -> ModelRegistryExport:
    """Deserialize a persisted payload, deriving the monotonic ``fetched_at``
    from the wall-clock ``refreshed_at`` so TTL semantics survive import."""
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("Malformed persisted model registry payload")
    if document.get("cleared") is True:
        return ModelRegistryExport(snapshot=None, metadata_models=None)
    snapshot_data = document.get("snapshot")
    if not isinstance(snapshot_data, dict):
        raise ValueError("Malformed persisted model registry snapshot")
    age_seconds = max(0.0, (utcnow() - to_utc_naive(refreshed_at)).total_seconds())
    snapshot = _decode_snapshot(snapshot_data, fetched_at=time.monotonic() - age_seconds)
    metadata_data = document.get("metadata_models")
    metadata_models = _decode_models(metadata_data) if isinstance(metadata_data, dict) else None
    return ModelRegistryExport(snapshot=snapshot, metadata_models=metadata_models)


async def persist_registry_snapshot(
    session: AsyncSession,
    *,
    encoded: EncodedRegistrySnapshot,
    leader_id: str | None,
) -> bool:
    """Atomically upsert the single snapshot row; returns True when followers must re-apply.

    Unchanged content only touches ``refreshed_at``/``leader_id`` (guarded by
    ``content_hash``) so snapshot age means "time since the leader last
    confirmed this catalog" without rewriting a potentially multi-MB payload.

    Returns True when the payload changed *or* when an existing row had aged past
    ``model_registry_snapshot_max_age_seconds`` before this refresh revived it:
    once a row expires, followers clear their local registry and reset their
    applied-content-hash marker, so an unchanged-content revival still needs a
    ``model_registry`` bump for them to re-apply within the cache-poll bound
    instead of waiting for the non-leader scheduler backstop. Returns False only
    when the row was already fresh and the content is unchanged.
    """
    existing = (
        await session.execute(
            select(
                ModelRegistrySnapshotRecord.content_hash,
                ModelRegistrySnapshotRecord.refreshed_at,
            ).where(ModelRegistrySnapshotRecord.id == _SNAPSHOT_ROW_ID)
        )
    ).first()
    if existing is not None and existing.content_hash == encoded.content_hash:
        result = await session.execute(
            update(ModelRegistrySnapshotRecord)
            .where(
                ModelRegistrySnapshotRecord.id == _SNAPSHOT_ROW_ID,
                ModelRegistrySnapshotRecord.content_hash == encoded.content_hash,
            )
            .values(
                schema_version=SCHEMA_VERSION,
                refreshed_at=encoded.refreshed_at,
                leader_id=leader_id,
            )
        )
        if isinstance(result, CursorResult) and result.rowcount == 1:
            await session.commit()
            max_age_seconds = get_settings().model_registry_snapshot_max_age_seconds
            prior_age_seconds = (utcnow() - to_utc_naive(existing.refreshed_at)).total_seconds()
            # An expired row means followers already dropped to the bootstrap
            # floor; treat the revival as bump-worthy so they re-apply promptly.
            return prior_age_seconds > max_age_seconds

    values = {
        "id": _SNAPSHOT_ROW_ID,
        "schema_version": SCHEMA_VERSION,
        "content_hash": encoded.content_hash,
        "payload": encoded.payload,
        "refreshed_at": encoded.refreshed_at,
        "leader_id": leader_id,
    }
    update_values = {key: value for key, value in values.items() if key != "id"}
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        stmt = (
            pg_insert(ModelRegistrySnapshotRecord)
            .values(**values)
            .on_conflict_do_update(index_elements=[ModelRegistrySnapshotRecord.id], set_=update_values)
        )
        await session.execute(stmt)
    elif dialect == "sqlite":
        stmt = (
            sqlite_insert(ModelRegistrySnapshotRecord)
            .values(**values)
            .on_conflict_do_update(index_elements=[ModelRegistrySnapshotRecord.id], set_=update_values)
        )
        await session.execute(stmt)
    else:
        existing = await session.scalar(
            select(ModelRegistrySnapshotRecord).where(ModelRegistrySnapshotRecord.id == _SNAPSHOT_ROW_ID)
        )
        if existing is None:
            session.add(ModelRegistrySnapshotRecord(**values))
        else:
            await session.execute(
                update(ModelRegistrySnapshotRecord)
                .where(ModelRegistrySnapshotRecord.id == _SNAPSHOT_ROW_ID)
                .values(**update_values)
            )
    await session.commit()
    logger.info(
        "Persisted model registry snapshot content_hash=%s payload_bytes=%d",
        encoded.content_hash,
        len(encoded.payload),
    )
    return True


async def _probe_header(session: AsyncSession) -> StoredSnapshotHeader | None:
    row = (
        await session.execute(
            select(
                ModelRegistrySnapshotRecord.schema_version,
                ModelRegistrySnapshotRecord.content_hash,
                ModelRegistrySnapshotRecord.refreshed_at,
            ).where(ModelRegistrySnapshotRecord.id == _SNAPSHOT_ROW_ID)
        )
    ).first()
    if row is None:
        return None
    schema_version, content_hash, refreshed_at = row
    return StoredSnapshotHeader(
        schema_version=schema_version,
        content_hash=content_hash,
        refreshed_at=refreshed_at,
    )


def _snapshot_age_seconds(header: StoredSnapshotHeader) -> float:
    return (utcnow() - to_utc_naive(header.refreshed_at)).total_seconds()


async def reconcile_model_registry_from_store(*, raise_on_error: bool = False) -> bool:
    """Apply the persisted snapshot to the local registry when it differs.

    Shared by lifespan startup, the ``model_registry`` invalidation callback,
    and the non-leader refresh-tick backstop. Returns True when a snapshot was
    applied.

    A load failure (transient DB read error or malformed payload) keeps the
    current in-memory state (bootstrap floor at worst) and logs a warning. When
    ``raise_on_error`` is True the failure is re-raised after logging so the
    caller can react to it: the ``model_registry`` invalidation callback passes
    ``raise_on_error=True`` so ``CacheInvalidationPoller._run_callbacks`` leaves
    the namespace version unacknowledged and retries on the next poll cycle
    (matching the ``account_routing`` refresh callback), rather than ACKing the
    bump and leaving this replica on the stale catalog until the non-leader
    scheduler backstop. The startup one-shot and refresh-tick backstop use the
    default ``raise_on_error=False`` so a transient failure never fails startup
    or the scheduler loop; those paths retry on their own cadence.

    The account-selection cache clears here are always local
    (``propagate=False``): reconcile only applies a change the leader already
    published (which bumped ``model_registry`` to reach every replica), and
    every replica clears its own selection cache on apply. Propagating would
    make each follower durably re-bump ``account_selection``, amplifying bus
    traffic without changing any peer's state.
    """
    registry = get_model_registry()
    try:
        max_age_seconds = get_settings().model_registry_snapshot_max_age_seconds
        async with get_background_session() as session:
            header = await _probe_header(session)
            if header is None:
                if registry.get_snapshot() is not None or registry.applied_content_hash is not None:
                    # The store has no snapshot row but this replica still
                    # carries registry state: either an unpublished leader-local
                    # refresh whose persist failed before leadership was lost,
                    # or a previously applied row that was deleted out from
                    # under us. Other replicas are serving the bootstrap floor,
                    # so drop the local state to converge with them until a
                    # leader publishes a snapshot.
                    await registry.clear()
                    get_account_selection_cache().invalidate(propagate=False)
                    logger.warning(
                        "Dropped local model registry state: no persisted snapshot row exists; "
                        "reverting to bootstrap floor"
                    )
                return False
            if header.schema_version != SCHEMA_VERSION:
                logger.warning(
                    "Ignoring persisted model registry snapshot with schema_version=%d (codec version %d)",
                    header.schema_version,
                    SCHEMA_VERSION,
                )
                return False
            if _snapshot_age_seconds(header) > max_age_seconds:
                if registry.get_snapshot() is not None or registry.applied_content_hash is not None:
                    # The only valid published row has aged past the staleness
                    # cap, so no replica can converge on it. This replica still
                    # carries registry state: either state imported from (or
                    # persisted to) the store, or an unpublished leader-local
                    # refresh whose persist failed before leadership was lost
                    # (no applied hash). Reconcile runs off the non-leader path
                    # and, under a prolonged upstream outage, on the leader's
                    # all-fetch-failed path, so a current or former leader
                    # holding a stale or unpublished catalog must not keep
                    # serving it while other replicas drop to the bootstrap
                    # floor. Drop the local state to converge until a leader
                    # publishes a fresh snapshot.
                    await registry.clear()
                    get_account_selection_cache().invalidate(propagate=False)
                    logger.warning(
                        "Dropped local model registry snapshot: persisted entry older than %ds "
                        "(refreshed_at=%s); reverting to bootstrap floor",
                        max_age_seconds,
                        header.refreshed_at,
                    )
                    return False
                logger.warning(
                    "Ignoring persisted model registry snapshot older than %ds (refreshed_at=%s)",
                    max_age_seconds,
                    header.refreshed_at,
                )
                return False
            if header.content_hash == registry.applied_content_hash:
                return False
            payload = await session.scalar(
                select(ModelRegistrySnapshotRecord.payload).where(ModelRegistrySnapshotRecord.id == _SNAPSHOT_ROW_ID)
            )
        if payload is None:
            return False
        state = decode_registry_payload(payload, refreshed_at=header.refreshed_at)
    except Exception:
        logger.warning("Failed to load persisted model registry snapshot", exc_info=True)
        if raise_on_error:
            raise
        return False

    await registry.import_state(state, content_hash=header.content_hash)
    get_account_selection_cache().invalidate(propagate=False)
    total_models = len(state.snapshot.models) if state.snapshot is not None else 0
    logger.info(
        "Applied persisted model registry snapshot content_hash=%s total_models=%d cleared=%s",
        header.content_hash,
        total_models,
        state.snapshot is None,
    )
    return True

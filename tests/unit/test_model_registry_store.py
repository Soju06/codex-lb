from __future__ import annotations

import dataclasses
import json
import time
from datetime import datetime, timedelta

import pytest

from app.core.openai.model_registry import (
    ModelRegistry,
    ModelRegistryExport,
    ModelRegistrySnapshot,
    ReasoningLevel,
    UpstreamModel,
)
from app.core.openai.model_registry_store import (
    SNAPSHOT_CODEC_FIELDS,
    UPSTREAM_MODEL_CODEC_FIELDS,
    decode_registry_payload,
    encode_registry_export,
)
from app.core.utils.time import utcnow

pytestmark = pytest.mark.unit


def _rich_model(slug: str, *, priority: int = 3) -> UpstreamModel:
    """A model with every field set to a non-default value so a codec gap
    cannot hide behind a default."""
    return UpstreamModel(
        slug=slug,
        display_name=f"{slug} display",
        description=f"Description of {slug}",
        context_window=272000,
        input_modalities=("text", "image"),
        supported_reasoning_levels=(
            ReasoningLevel(effort="medium", description="balanced"),
            ReasoningLevel(effort="xhigh", description="deep"),
        ),
        default_reasoning_level="xhigh",
        supports_reasoning_summaries=True,
        support_verbosity=True,
        default_verbosity="medium",
        prefer_websockets=True,
        supports_parallel_tool_calls=True,
        supported_in_api=False,
        minimal_client_version="0.144.0",
        priority=priority,
        available_in_plans=frozenset({"pro", "enterprise"}),
        base_instructions="You are a codex model." * 4,
        source_kind="subscription",
        source_id="src-1",
        raw={"visibility": "list", "supported_service_tiers": [{"id": "priority"}], "nested": {"a": [1, 2]}},
    )


def _rich_snapshot(*, fetched_at: float | None = None) -> ModelRegistrySnapshot:
    models = {"gpt-new": _rich_model("gpt-new"), "gpt-pro-only": _rich_model("gpt-pro-only", priority=7)}
    return ModelRegistrySnapshot(
        models=models,
        model_plans={"gpt-new": frozenset({"plus", "pro"}), "gpt-pro-only": frozenset({"pro"})},
        plan_models={"plus": frozenset({"gpt-new"}), "pro": frozenset({"gpt-new", "gpt-pro-only"})},
        model_service_tier_plans={"gpt-new": {"priority": frozenset({"pro"})}},
        model_service_tier_accounts={"gpt-new": {"priority": frozenset({"acc-1"})}},
        account_plans={"acc-1": "pro", "acc-2": "plus"},
        fetched_at=fetched_at if fetched_at is not None else time.monotonic(),
        model_accounts={"gpt-new": frozenset({"acc-1", "acc-2"})},
        account_catalogs_authoritative=True,
        bootstrap_floor_active=True,
        suppressed_model_slugs=frozenset({"gpt-withdrawn"}),
    )


def _assert_model_field_equal(original: UpstreamModel, decoded: UpstreamModel) -> None:
    # ``raw`` has compare=False on the dataclass, so equality alone would let a
    # codec gap in ``raw`` slip through; compare every field explicitly.
    for field in dataclasses.fields(UpstreamModel):
        assert getattr(decoded, field.name) == getattr(original, field.name), field.name


class TestCodecFieldCompleteness:
    def test_upstream_model_codec_covers_every_dataclass_field(self) -> None:
        assert UPSTREAM_MODEL_CODEC_FIELDS == {f.name for f in dataclasses.fields(UpstreamModel)}

    def test_snapshot_codec_covers_every_dataclass_field(self) -> None:
        assert SNAPSHOT_CODEC_FIELDS == {f.name for f in dataclasses.fields(ModelRegistrySnapshot)}


class TestRoundTrip:
    def test_round_trip_is_field_complete(self) -> None:
        snapshot = _rich_snapshot()
        metadata_models = {"gpt-old-metadata": _rich_model("gpt-old-metadata", priority=1)}
        export = ModelRegistryExport(snapshot=snapshot, metadata_models=metadata_models)

        encoded = encode_registry_export(export)
        decoded = decode_registry_payload(encoded.payload, refreshed_at=encoded.refreshed_at)

        assert decoded.snapshot is not None
        for field in dataclasses.fields(ModelRegistrySnapshot):
            if field.name in {"models", "fetched_at"}:
                continue
            assert getattr(decoded.snapshot, field.name) == getattr(snapshot, field.name), field.name
        assert set(decoded.snapshot.models) == set(snapshot.models)
        for slug, original in snapshot.models.items():
            _assert_model_field_equal(original, decoded.snapshot.models[slug])
        assert decoded.metadata_models is not None
        assert set(decoded.metadata_models) == {"gpt-old-metadata"}
        _assert_model_field_equal(metadata_models["gpt-old-metadata"], decoded.metadata_models["gpt-old-metadata"])

    def test_round_trip_without_metadata_models(self) -> None:
        export = ModelRegistryExport(snapshot=_rich_snapshot(), metadata_models=None)
        encoded = encode_registry_export(export)
        decoded = decode_registry_payload(encoded.payload, refreshed_at=encoded.refreshed_at)
        assert decoded.snapshot is not None
        assert decoded.metadata_models is None

    def test_cleared_marker_round_trip(self) -> None:
        encoded = encode_registry_export(ModelRegistryExport(snapshot=None, metadata_models=None))
        decoded = decode_registry_payload(encoded.payload, refreshed_at=encoded.refreshed_at)
        assert decoded.snapshot is None
        assert decoded.metadata_models is None

    def test_malformed_payload_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            decode_registry_payload("[]", refreshed_at=utcnow())
        with pytest.raises(ValueError):
            decode_registry_payload('{"cleared": false, "snapshot": 42}', refreshed_at=utcnow())


def _encoded_document(snapshot: ModelRegistrySnapshot) -> tuple[dict, datetime]:
    encoded = encode_registry_export(ModelRegistryExport(snapshot=snapshot, metadata_models=None))
    return json.loads(encoded.payload), encoded.refreshed_at


class TestMalformedSetBackedFields:
    """A set/mapping-backed field persisted with a wrong-typed value MUST reject
    the whole decode rather than silently dropping the entry, so the poller
    leaves the ``model_registry`` bump unacknowledged and retries instead of
    applying a partial catalog (e.g. a model whose plan gating vanished)."""

    @pytest.mark.parametrize(
        ("path", "bad_value"),
        [
            # A dict where a list of slugs is expected -- the finding's example.
            (("model_plans", "gpt-new"), {"gpt-x": "pro"}),
            # A bare string where a list of slugs is expected.
            (("model_plans", "gpt-new"), "pro"),
            (("plan_models", "pro"), "gpt-new"),
            (("model_accounts", "gpt-new"), "acc-1"),
            # Tier maps delegate to the same set decoder.
            (("model_service_tier_plans", "gpt-new", "priority"), "pro"),
            (("model_service_tier_accounts", "gpt-new", "priority"), "acc-1"),
        ],
    )
    def test_wrong_typed_set_backed_field_raises(self, path: tuple[str, ...], bad_value: object) -> None:
        document, refreshed_at = _encoded_document(_rich_snapshot())
        target = document["snapshot"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = bad_value
        with pytest.raises(ValueError):
            decode_registry_payload(json.dumps(document), refreshed_at=refreshed_at)

    def test_wrong_typed_model_entry_raises(self) -> None:
        document, refreshed_at = _encoded_document(_rich_snapshot())
        document["snapshot"]["models"]["gpt-new"] = "not-a-model"
        with pytest.raises(ValueError):
            decode_registry_payload(json.dumps(document), refreshed_at=refreshed_at)

    def test_wrong_typed_reasoning_level_raises(self) -> None:
        document, refreshed_at = _encoded_document(_rich_snapshot())
        document["snapshot"]["models"]["gpt-new"]["supported_reasoning_levels"] = ["medium"]
        with pytest.raises(ValueError):
            decode_registry_payload(json.dumps(document), refreshed_at=refreshed_at)

    def test_empty_set_backed_maps_are_allowed(self) -> None:
        # Genuinely-absent/empty set mappings decode successfully (empty != malformed).
        snapshot = dataclasses.replace(
            _rich_snapshot(),
            model_plans={},
            plan_models={},
            model_service_tier_plans={},
            model_service_tier_accounts={},
            model_accounts={"gpt-new": frozenset()},
        )
        encoded = encode_registry_export(ModelRegistryExport(snapshot=snapshot, metadata_models=None))
        decoded = decode_registry_payload(encoded.payload, refreshed_at=encoded.refreshed_at)
        assert decoded.snapshot is not None
        assert decoded.snapshot.model_plans == {}
        assert decoded.snapshot.model_accounts == {"gpt-new": frozenset()}


class TestContentHash:
    def test_hash_stable_across_refresh_ticks_with_identical_catalog(self) -> None:
        first = encode_registry_export(
            ModelRegistryExport(snapshot=_rich_snapshot(fetched_at=1.0), metadata_models=None)
        )
        second = encode_registry_export(
            ModelRegistryExport(snapshot=_rich_snapshot(fetched_at=9999.0), metadata_models=None)
        )
        assert first.content_hash == second.content_hash

    def test_hash_changes_when_catalog_changes(self) -> None:
        base = _rich_snapshot()
        changed = dataclasses.replace(base, suppressed_model_slugs=frozenset({"gpt-withdrawn", "gpt-more"}))
        first = encode_registry_export(ModelRegistryExport(snapshot=base, metadata_models=None))
        second = encode_registry_export(ModelRegistryExport(snapshot=changed, metadata_models=None))
        assert first.content_hash != second.content_hash


class TestFetchedAtFidelity:
    def test_refreshed_at_reflects_snapshot_age_on_encode(self) -> None:
        snapshot = _rich_snapshot(fetched_at=time.monotonic() - 120.0)
        encoded = encode_registry_export(ModelRegistryExport(snapshot=snapshot, metadata_models=None))
        age = (utcnow() - encoded.refreshed_at).total_seconds()
        assert 118.0 <= age <= 125.0

    def test_decode_derives_monotonic_fetched_at_from_wall_clock(self) -> None:
        snapshot = _rich_snapshot()
        encoded = encode_registry_export(ModelRegistryExport(snapshot=snapshot, metadata_models=None))
        stale_refreshed_at = utcnow() - timedelta(seconds=400)
        decoded = decode_registry_payload(encoded.payload, refreshed_at=stale_refreshed_at)
        assert decoded.snapshot is not None
        age = time.monotonic() - decoded.snapshot.fetched_at
        assert 395.0 <= age <= 410.0

    async def test_needs_refresh_semantics_survive_import(self) -> None:
        snapshot = _rich_snapshot()
        encoded = encode_registry_export(ModelRegistryExport(snapshot=snapshot, metadata_models=None))

        registry = ModelRegistry(ttl_seconds=300.0)
        fresh = decode_registry_payload(encoded.payload, refreshed_at=utcnow() - timedelta(seconds=10))
        await registry.import_state(fresh, content_hash=encoded.content_hash)
        assert registry.needs_refresh() is False

        stale = decode_registry_payload(encoded.payload, refreshed_at=utcnow() - timedelta(seconds=3600))
        await registry.import_state(stale, content_hash=encoded.content_hash)
        assert registry.needs_refresh() is True


class TestRegistryExportImport:
    async def test_export_then_import_reproduces_gating_surfaces(self) -> None:
        source = ModelRegistry(ttl_seconds=300.0)
        snapshot = _rich_snapshot()
        await source.import_state(ModelRegistryExport(snapshot=snapshot, metadata_models=None), content_hash="hash-a")

        encoded = encode_registry_export(await source.export_state())
        target = ModelRegistry(ttl_seconds=300.0)
        decoded = decode_registry_payload(encoded.payload, refreshed_at=encoded.refreshed_at)
        await target.import_state(decoded, content_hash=encoded.content_hash)

        assert target.applied_content_hash == encoded.content_hash
        assert target.is_suppressed_model("gpt-withdrawn") is True
        assert target.plan_types_for_model("gpt-pro-only") == frozenset({"pro"})
        assert set(target.get_models_with_fallback()) >= {"gpt-new", "gpt-pro-only"}

    async def test_clear_resets_applied_content_hash(self) -> None:
        registry = ModelRegistry(ttl_seconds=300.0)
        await registry.import_state(
            ModelRegistryExport(snapshot=_rich_snapshot(), metadata_models=None), content_hash="hash-b"
        )
        assert registry.applied_content_hash == "hash-b"
        await registry.clear()
        assert registry.applied_content_hash is None
        assert registry.get_snapshot() is None

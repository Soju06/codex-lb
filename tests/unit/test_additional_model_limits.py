from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.proxy.additional_model_limits import (
    get_additional_display_label_for_model,
    get_additional_model_limit,
    get_additional_quota_key_for_model_id,
)
from app.modules.usage.additional_quota_keys import (
    canonicalize_additional_quota_key,
    clear_additional_quota_registry_cache,
    reload_additional_quota_registry,
)

pytestmark = pytest.mark.unit


def test_get_additional_model_limit_returns_seeded_mapping() -> None:
    resolved = get_additional_model_limit("gpt-5.3-codex-spark")

    assert resolved is not None
    assert resolved.model == "gpt-5.3-codex-spark"
    assert resolved.quota_key == "codex_spark"
    assert resolved.display_label == "GPT-5.3-Codex-Spark"


def test_get_additional_model_limit_normalizes_case_and_whitespace() -> None:
    resolved = get_additional_model_limit("  GPT-5.3-CODEX-SPARK  ")

    assert resolved is not None
    assert resolved.quota_key == "codex_spark"
    assert resolved.display_label == "GPT-5.3-Codex-Spark"


def test_get_additional_quota_key_for_model_returns_none_for_unmapped_model() -> None:
    assert get_additional_quota_key_for_model_id("gpt-5.3-codex") is None
    assert get_additional_quota_key_for_model_id(None) is None


def test_get_additional_display_label_for_model_returns_seeded_label() -> None:
    assert get_additional_display_label_for_model("gpt-5.3-codex-spark") == "GPT-5.3-Codex-Spark"
    assert get_additional_display_label_for_model("gpt-5.3-codex") is None


def test_canonicalize_additional_quota_key_accepts_known_upstream_aliases() -> None:
    assert canonicalize_additional_quota_key(limit_name="codex_other") == "codex_spark"
    assert canonicalize_additional_quota_key(limit_name="GPT-5.3-Codex-Spark") == "codex_spark"
    assert canonicalize_additional_quota_key(metered_feature="codex_bengalfox") == "codex_spark"


def test_canonicalize_additional_quota_key_normalizes_unknown_aliases() -> None:
    assert canonicalize_additional_quota_key(limit_name="O-Pro") == "o_pro"
    assert canonicalize_additional_quota_key(metered_feature="Deep Research") == "deep_research"


def test_additional_quota_routing_policy_resolves_limit_name_alias() -> None:
    assert get_additional_quota_routing_policy("codex_other") == "burn_first"


def test_additional_quota_routing_policy_overrides_resolve_limit_name_alias() -> None:
    assert get_additional_quota_routing_policy("codex_other", overrides={"codex_other": "preserve"}) == "preserve"


def test_registry_normalizes_configured_quota_key(monkeypatch, tmp_path: Path) -> None:
    registry = tmp_path / "additional_quota_registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "quota_key": " Spark-Enterprise ",
                    "display_label": "Spark Enterprise",
                    "model_ids": ["gpt-5.3-codex-spark"],
                    "limit_name_aliases": ["codex_other"],
                    "metered_feature_aliases": ["codex_bengalfox"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry))
    clear_additional_quota_registry_cache()

    resolved = get_additional_model_limit("gpt-5.3-codex-spark")

    assert resolved is not None
    assert resolved.quota_key == "spark_enterprise"
    assert canonicalize_additional_quota_key(limit_name="codex_other") == "spark_enterprise"
    assert get_additional_display_label_for_model("gpt-5.3-codex-spark") == "Spark Enterprise"


def test_registry_resolves_legacy_quota_key_alias(monkeypatch, tmp_path: Path) -> None:
    registry = tmp_path / "additional_quota_registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "quota_key": "spark_enterprise",
                    "quota_key_aliases": ["codex_spark"],
                    "display_label": "Spark Enterprise",
                    "model_ids": ["gpt-5.3-codex-spark"],
                    "limit_name_aliases": ["codex_other"],
                    "metered_feature_aliases": ["codex_bengalfox"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry))
    clear_additional_quota_registry_cache()

    assert canonicalize_additional_quota_key(quota_key="codex_spark") == "spark_enterprise"
    assert canonicalize_additional_quota_key(limit_name="codex_other") == "spark_enterprise"


def test_registry_reloads_when_config_file_changes(monkeypatch, tmp_path: Path) -> None:
    registry = tmp_path / "additional_quota_registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "quota_key": "codex_spark",
                    "display_label": "GPT-5.3-Codex-Spark",
                    "model_ids": ["gpt-5.3-codex-spark"],
                    "limit_name_aliases": ["codex_other"],
                    "metered_feature_aliases": ["codex_bengalfox"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry))
    clear_additional_quota_registry_cache()

    assert canonicalize_additional_quota_key(limit_name="codex_other") == "codex_spark"

    registry.write_text(
        json.dumps(
            [
                {
                    "quota_key": "codex_spark_v2",
                    "display_label": "GPT-5.3-Codex-Spark",
                    "model_ids": ["gpt-5.3-codex-spark"],
                    "limit_name_aliases": ["codex_other"],
                    "metered_feature_aliases": ["codex_bengalfox"],
                }
            ]
        ),
        encoding="utf-8",
    )

    assert canonicalize_additional_quota_key(limit_name="codex_other") == "codex_spark"

    status = reload_additional_quota_registry()

    assert status.definition_count == 1
    assert canonicalize_additional_quota_key(limit_name="codex_other") == "codex_spark_v2"


def test_registry_rejects_duplicate_aliases(monkeypatch, tmp_path: Path) -> None:
    registry = tmp_path / "additional_quota_registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "quota_key": "first_quota",
                    "display_label": "First",
                    "limit_name_aliases": ["shared-alias"],
                },
                {
                    "quota_key": "second_quota",
                    "display_label": "Second",
                    "limit_name_aliases": ["shared-alias"],
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry))
    clear_additional_quota_registry_cache()

    with pytest.raises(ValueError, match="duplicate additional quota alias"):
        canonicalize_additional_quota_key(limit_name="shared-alias")


def test_reload_additional_quota_registry_returns_status(monkeypatch, tmp_path: Path) -> None:
    registry = tmp_path / "additional_quota_registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "quota_key": "codex_spark",
                    "display_label": "GPT-5.3-Codex-Spark",
                    "model_ids": ["gpt-5.3-codex-spark"],
                    "limit_name_aliases": ["codex_other"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry))

    status = reload_additional_quota_registry()

    assert status.path == registry.resolve()
    assert status.definition_count == 1

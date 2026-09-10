from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.usage.pricing import ModelPrice
from app.core.usage.pricing_catalog import encode_snapshot
from scripts import update_upstream_metadata as script


async def test_generation_preserves_unchanged_snapshot_and_fails_before_writing_partial_data(tmp_path, monkeypatch):
    path = tmp_path / "pricing.json"
    original = encode_snapshot({"gpt-test": ModelPrice(10, 50, 1)})
    path.write_text(original)
    monkeypatch.setattr(script, "BUNDLE_PATH", path)
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setattr(script.CodexVersionCache, "_fetch_latest_version", AsyncMock(return_value="1.2.3"))
    primary = {
        "openai": {
            "models": {
                "gpt-test": {"modalities": {"output": ["text"]}, "cost": {"input": 10, "output": 50, "cache_read": 1}}
            }
        }
    }
    secondary = {
        "gpt-test": {
            "litellm_provider": "openai",
            "mode": "chat",
            "input_cost_per_token": 1e-5,
            "output_cost_per_token": 5e-5,
            "cache_read_input_token_cost": 1e-6,
        }
    }
    monkeypatch.setattr(script, "fetch", lambda url: primary if url == script.MODELS_DEV_URL else secondary)
    await script.main()
    assert path.read_text() == original
    version_path = tmp_path / "app/core/clients/codex_version_snapshot.py"
    assert 'CODEX_VERSION = "1.2.3"' in version_path.read_text()

    def fail_secondary(url):
        if url == script.MODELS_DEV_URL:
            return primary
        raise ValueError("unavailable")

    monkeypatch.setattr(script, "fetch", fail_secondary)
    with pytest.raises(ValueError, match="unavailable"):
        await script.main()
    assert path.read_text() == original
    assert 'CODEX_VERSION = "1.2.3"' in version_path.read_text()

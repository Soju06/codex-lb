from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import worktree_root  # noqa: E402, I001


FACTORY_REPO = Path("/Volumes/StudioExt/repos/of")
SUBSCRIPTION_BARE = {
    "claude-fable-5-1",
    "claude-opus-5",
    "gpt-6-astra",
    "gpt-5.6-luna",
}


def _factory_candidates() -> dict[str, dict]:
    raw = subprocess.check_output(
        ["git", "-C", str(FACTORY_REPO), "show", "origin/main:schema/defaults/factory.json"]
    )
    return json.loads(raw)["candidates"]


def main() -> None:
    from app.modules.usage.additional_quota_keys import (
        clear_additional_quota_registry_cache,
        get_additional_quota_definition_for_model,
    )

    clear_additional_quota_registry_cache()
    candidates = _factory_candidates()
    assert candidates, "factory.json candidates missing"
    for key, spec in candidates.items():
        for identifier in {key, spec.get("canonical_slug"), spec.get("model_id")}:
            if not identifier:
                continue
            definition = get_additional_quota_definition_for_model(identifier)
            assert definition is not None, f"unregistered identifier {identifier!r}"
            if "/" in identifier:
                assert definition.quota_key == "openrouter_credits", (
                    f"{identifier!r} resolved to {definition.quota_key}"
                )
            if identifier in SUBSCRIPTION_BARE:
                assert definition.quota_key != "openrouter_credits"
                assert definition.quota_key not in {"glm_coding", "kimi_coding"}
    glm_qualified = get_additional_quota_definition_for_model("z-ai/glm-5.2")
    assert glm_qualified is not None and glm_qualified.quota_key == "openrouter_credits"
    glm_bare = get_additional_quota_definition_for_model("glm-5.2")
    assert glm_bare is not None and glm_bare.quota_key == "glm_coding"
    door = get_additional_quota_definition_for_model("qwen/qwen3-coder-30b-a3b-instruct")
    assert door is not None
    assert "glm_5_2" not in door.model_ids
    assert worktree_root().name == "of-wt-account-store"


if __name__ == "__main__":
    main()

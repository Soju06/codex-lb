from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import worktree_root  # noqa: E402


def main() -> None:
    from app.core.providers.openrouter import SUPPORTS_UPSTREAM_COUNT_TOKENS

    profile = json.loads((worktree_root() / "config/openrouter_wire_profile.json").read_text())
    assert profile["decided_by"] == "live_call"
    assert profile["messages"]["status"] == 200
    assert profile["count_tokens"]["status"] == 404
    assert profile["wire"] == "anthropic_messages"
    assert profile["anthropic_compat_is_correct_seam"] is True
    assert profile["supports_upstream_count_tokens"] is False
    assert profile["escape_hatch_needed"] is True
    assert SUPPORTS_UPSTREAM_COUNT_TOKENS is False
    assert SUPPORTS_UPSTREAM_COUNT_TOKENS == profile["supports_upstream_count_tokens"]


if __name__ == "__main__":
    main()

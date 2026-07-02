"""Hardcoded catalog of Claude model ids that are available to the OAuth pool.

The list is intentionally static. Anthropic publishes new model ids via the
``https://api.anthropic.com/v1/models`` endpoint for API-key callers, but
Max/Pro/Team subscription OAuth callers do NOT have access to that endpoint at
runtime. Instead we ship a pinned allow-list of model ids that are known to be
eligible for the subscription tier as of the change date.

Sources (verified 2026-07-01):
- ``claude-opus-4-8`` — anthropic.com/news/claude-opus-4-8, AWS Bedrock model card
- ``claude-sonnet-4-6`` — Hidekazu timeline, anthropic.com
- ``claude-haiku-4-5-20251001`` — Hidekazu timeline, ``platform.claude.com``
  model-ids-and-versions docs (date suffix is the snapshot identifier per the
  Anthropic versioning convention)

Any id in :data:`KNOWN_CLAUDE_MODELS` MUST be a currently-published Anthropic
model id; see ``is_known_claude_model`` for runtime validation used by the
proxy and dashboard layers.
"""

from __future__ import annotations

from typing import Any

KNOWN_CLAUDE_MODELS: frozenset[str] = frozenset(
    {
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    }
)


def list_claude_models() -> dict[str, Any]:
    """Return the catalog in Anthropic's ``GET /v1/models`` response shape.

    The envelope is::

        {
          "object": "list",
          "data": [
            {"id": "...", "object": "model", "display_name": "...",
             "type": "model"},
            ...
          ]
        }

    ``data`` is sorted by id so callers (and snapshot tests) see a stable
    order regardless of insertion order in :data:`KNOWN_CLAUDE_MODELS`.
    """
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "display_name": model_id,
                "type": "model",
            }
            for model_id in sorted(KNOWN_CLAUDE_MODELS)
        ],
    }


def is_known_claude_model(model_id: str) -> bool:
    """Return True iff ``model_id`` is in the Claude subscription allow-list."""
    return model_id in KNOWN_CLAUDE_MODELS

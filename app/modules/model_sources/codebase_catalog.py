"""Explicit native model mappings, verified with the local login on 2026-09-10.

The gateway has no working /models endpoint. This is a maintained preset, not
live discovery; newly available Coco agent names are never guessed as API IDs.
"""

from __future__ import annotations

import json

from app.modules.model_sources.schemas import ModelSourceModelInput

# Context limits are the inspected native provider's published model metadata.
_MODELS = (
    ("gpt-5.4", "responses", 272_000),
    ("gpt-5.2", "responses", 272_000),
    ("seed-code-preview", "chat", 256_000),
    ("doubao-seed-2.0-code", "chat", 256_000),
    ("doubao-seed-1.8", "chat", 256_000),
    ("deepseek-v3.1", "chat", 128_000),
    ("kimi-k2.6", "chat", 200_000),
    ("kimi-k2.5", "chat", 200_000),
    ("qwen3.6-plus", "chat", 128_000),
    ("qwen3.5-plus", "chat", 128_000),
)


def model_presets() -> list[ModelSourceModelInput]:
    return [
        ModelSourceModelInput(
            model="codebase/" + model,
            display_name="Codebase " + model,
            context_window=window,
            supports_streaming=True,
            supports_tools=True,
            is_enabled=False,
            raw_metadata_json=json.dumps({"native_backend": backend, "verification_date": "2026-09-10"}),
        )
        for model, backend, window in _MODELS
    ]

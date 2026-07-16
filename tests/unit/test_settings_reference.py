"""Drift guards for the generated settings reference page (issue #1340).

Guards three contracts:

- ``docs/reference/settings.md`` is byte-identical to what the generator
  renders from the current ``Settings`` surface,
- the settings surface does not silently grow past its ratchet,
- ``.env.example`` never states an active value that contradicts the code
  default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest

from app.core.config.settings import Settings
from scripts.generate_settings_reference import OUTPUT_PATH, render_settings_reference


def _isolated_settings(**overrides: Any) -> Settings:
    """Build Settings from code defaults only.

    Strips ``CODEX_LB_*`` and the bare ``PORT`` variable from the process
    environment and disables env-file loading, so a developer's local
    ``.env.local`` or exported variables can never mask (or fake) a drift
    between ``.env.example`` and the code.
    """
    clean = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_LB_") and k != "PORT"}
    with mock.patch.dict(os.environ, clean, clear=True):
        # ``_env_file`` is a documented pydantic-settings init kwarg that its
        # type stubs do not expose; ty flags it as unknown.
        return Settings(_env_file=None, **overrides)  # ty: ignore[unknown-argument]


pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

# Ratchet on the settings surface (issue #1340, PRINCIPLES.md P2). Lower this
# number when fields are removed; never raise it without a simplicity-budget
# discussion — every new CODEX_LB_* setting needs a why-not-a-default
# justification per CONTRIBUTING.md's simplicity gates.
MAX_SETTINGS_FIELDS = 115


def test_generated_settings_reference_matches_code() -> None:
    """Regenerate-and-diff: the checked-in page must match the code exactly.

    On failure run: uv run python scripts/generate_settings_reference.py
    """
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render_settings_reference()


def test_settings_reference_page_is_checked_in_under_docs() -> None:
    assert OUTPUT_PATH == REPO_ROOT / "docs" / "reference" / "settings.md"
    assert OUTPUT_PATH.is_file()


def test_settings_surface_ratchet() -> None:
    assert len(Settings.model_fields) <= MAX_SETTINGS_FIELDS, (
        f"Settings grew to {len(Settings.model_fields)} fields (ratchet: {MAX_SETTINGS_FIELDS}). "
        "New settings need a simplicity-budget discussion (PRINCIPLES.md P2, issue #1340); "
        "lower MAX_SETTINGS_FIELDS when fields are removed."
    )


def _uncommented_assignments(text: str) -> list[tuple[str, str]]:
    assignments: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        assert sep == "=", f".env.example contains a non-assignment line: {line!r}"
        assignments.append((key.strip(), value.strip()))
    return assignments


def test_env_example_uncommented_values_match_code_defaults() -> None:
    """Every active KEY=value in .env.example must equal the code default.

    Commented lines are exempt; today the file is fully commented out and
    copying it verbatim must change nothing (user-documentation spec).
    """
    defaults = _isolated_settings()
    for key, value in _uncommented_assignments(ENV_EXAMPLE_PATH.read_text(encoding="utf-8")):
        if key == "PORT":
            assert value == "2455", f".env.example sets PORT={value}, but the code default is 2455"
            continue
        assert key.startswith("CODEX_LB_"), f".env.example sets unknown env var {key}"
        field_name = key.removeprefix("CODEX_LB_").lower()
        assert field_name in Settings.model_fields, f".env.example sets unknown setting {key}"
        # The raw env-file string is validated through the field's own
        # validators/coercion, exactly as pydantic-settings would apply it.
        candidate = _isolated_settings(**cast("dict[str, Any]", {field_name: value}))
        assert getattr(candidate, field_name) == getattr(defaults, field_name), (
            f".env.example sets {key}={value}, which differs from the code default "
            f"{getattr(defaults, field_name)!r}; keep the line commented or fix the value"
        )

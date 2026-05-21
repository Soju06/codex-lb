from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def load_sync_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "sync_codex_ok_labels.py"
    spec = importlib.util.spec_from_file_location("sync_codex_ok_labels", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def decision(module: ModuleType, **overrides: Any) -> Any:
    values = {
        "repo": "Soju06/codex-lb",
        "number": 714,
        "head_sha": "a" * 40,
        "has_ok_label": True,
        "wants_ok_label": False,
        "ok_action": "remove",
        "has_needs_work_label": False,
        "wants_needs_work_label": False,
        "needs_work_action": "keep",
        "legacy_labels": frozenset(),
        "reason": "checks are pending",
        "review_url": None,
        "review_state": "clean",
        "checks_state": "pending",
        "merge_state": "CLEAN",
        "trigger_codex_review": False,
        "approve_workflow_run_ids": (),
    }
    values.update(overrides)
    return module.SyncDecision(**values)


def test_apply_decision_tolerates_github_app_write_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()

    def deny_write(*_args: Any, **_kwargs: Any) -> None:
        raise module.GhError("gh: Resource not accessible by integration (HTTP 403)")

    monkeypatch.setattr(module, "gh_api", deny_write)

    warnings = module.apply_decision(decision(module), tolerate_permission_errors=True)

    assert len(warnings) == 1
    assert "remove 🤖 codex: ok from Soju06/codex-lb#714" in warnings[0]
    assert "Resource not accessible by integration" in warnings[0]


def test_apply_decision_still_fails_on_write_denial_without_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()

    def deny_write(*_args: Any, **_kwargs: Any) -> None:
        raise module.GhError("gh: Resource not accessible by integration (HTTP 403)")

    monkeypatch.setattr(module, "gh_api", deny_write)

    with pytest.raises(module.GhError):
        module.apply_decision(decision(module), tolerate_permission_errors=False)


def test_trigger_codex_review_tolerates_github_app_write_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()

    def deny_write(*_args: Any, **_kwargs: Any) -> None:
        raise module.GhError("gh: Resource not accessible by integration (HTTP 403)")

    monkeypatch.setattr(module, "run_gh", deny_write)
    request_review = decision(module, trigger_codex_review=True, ok_action="keep")

    warnings = module.trigger_codex_review(
        request_review,
        body="@codex review",
        tolerate_permission_errors=True,
    )

    assert len(warnings) == 1
    assert "request Codex review on Soju06/codex-lb#714" in warnings[0]


def test_workflow_prefers_privileged_token_and_enables_tolerant_apply() -> None:
    workflow = Path(".github/workflows/codex-review-labels.yml").read_text(encoding="utf-8")

    assert "secrets.CODEX_LABEL_SYNC_TOKEN || secrets.RELEASE_PLEASE_TOKEN || github.token" in workflow
    assert workflow.count("--tolerate-write-permission-errors") == 2

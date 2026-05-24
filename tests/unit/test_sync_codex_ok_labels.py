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


def test_apply_decision_treats_missing_label_delete_as_done(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()

    calls: list[tuple[str, str]] = []

    def missing_label(path: str, *, method: str = "GET", **_kwargs: Any) -> None:
        calls.append((method, path))
        raise module.GhError("gh: Label does not exist (HTTP 404)")

    monkeypatch.setattr(module, "gh_api", missing_label)

    warnings = module.apply_decision(decision(module), tolerate_permission_errors=False)

    assert warnings == ()
    assert calls == [
        (
            "DELETE",
            "/repos/Soju06/codex-lb/issues/714/labels/%F0%9F%A4%96%20codex%3A%20ok",
        )
    ]


def test_apply_decision_does_not_swallow_unrelated_delete_404(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()

    def missing_resource(*_args: Any, **_kwargs: Any) -> None:
        raise module.GhError("gh: Not Found (HTTP 404)")

    monkeypatch.setattr(module, "gh_api", missing_resource)

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
    assert workflow.count("--tolerate-read-errors") == 1


def test_main_tolerates_read_errors_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710, 714])

    def fake_decide_pr(_repo: str, number: int, **_kwargs: Any) -> Any:
        if number == 710:
            raise module.GhError("gh: HTTP 502")
        return decision(module, number=number)

    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--tolerate-read-errors"])

    captured = capsys.readouterr()
    assert result == 0
    assert "Soju06/codex-lb#710: gh: HTTP 502" in captured.err
    assert "dry-run Soju06/codex-lb#714" in captured.out


def test_main_fails_tolerant_run_when_every_pr_read_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710, 714])
    monkeypatch.setattr(
        module,
        "decide_pr",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(module.GhError("gh: HTTP 502")),
    )

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--tolerate-read-errors"])

    captured = capsys.readouterr()
    assert result == 1
    assert "all selected PRs failed classification" in captured.err


def test_main_fails_read_errors_without_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()

    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710])
    monkeypatch.setattr(
        module,
        "decide_pr",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(module.GhError("gh: HTTP 502")),
    )

    assert module.main(["--repo", "Soju06/codex-lb", "--all-open"]) == 1


def test_main_fails_apply_errors_even_with_read_error_tolerance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [714])
    monkeypatch.setattr(module, "decide_pr", lambda *_args, **_kwargs: decision(module))

    def fail_apply(*_args: Any, **_kwargs: Any) -> tuple[str, ...]:
        raise module.GhError("gh: HTTP 500 while writing labels")

    monkeypatch.setattr(module, "apply_decision", fail_apply)

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply", "--tolerate-read-errors"])

    captured = capsys.readouterr()
    assert result == 1
    assert "Soju06/codex-lb#714: gh: HTTP 500 while writing labels" in captured.err

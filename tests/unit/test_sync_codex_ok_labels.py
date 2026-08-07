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
        "has_needs_rebase_label": False,
        "wants_needs_rebase_label": False,
        "needs_rebase_action": "keep",
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


def codex_review_request(author: str, created_at: str) -> dict[str, Any]:
    return {
        "__typename": "IssueComment",
        "author": {"login": author},
        "bodyText": "@codex review",
        "createdAt": created_at,
        "url": f"https://github.test/request/{created_at}",
    }


def codex_issue_comment(body: str, created_at: str) -> dict[str, Any]:
    return {
        "__typename": "IssueComment",
        "author": {"login": "chatgpt-codex-connector"},
        "bodyText": body,
        "createdAt": created_at,
        "url": f"https://github.test/codex/{created_at}",
    }


@pytest.mark.parametrize("merge_state", ["CONFLICTING", "DIRTY"])
def test_needs_rebase_label_target_adds_for_confirmed_conflicts(merge_state: str) -> None:
    module = load_sync_module()

    assert module.needs_rebase_label_target(merge_state, has_label=False) is True


@pytest.mark.parametrize("merge_state", ["BEHIND", "BLOCKED", "CLEAN", "DRAFT", "HAS_HOOKS", "UNSTABLE"])
def test_needs_rebase_label_target_removes_for_known_non_conflict_states(merge_state: str) -> None:
    module = load_sync_module()

    assert module.needs_rebase_label_target(merge_state, has_label=True) is False


@pytest.mark.parametrize("has_label", [False, True])
def test_needs_rebase_label_target_preserves_unknown_state(has_label: bool) -> None:
    module = load_sync_module()

    assert module.needs_rebase_label_target("UNKNOWN", has_label=has_label) is has_label


def test_apply_decision_adds_needs_rebase_label(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    calls: list[tuple[str, str, Any | None]] = []

    def capture_write(path: str, *, method: str = "GET", input_json: Any | None = None) -> None:
        calls.append((method, path, input_json))

    monkeypatch.setattr(module, "gh_api", capture_write)

    warnings = module.apply_decision(
        decision(
            module,
            ok_action="keep",
            has_needs_rebase_label=False,
            wants_needs_rebase_label=True,
            needs_rebase_action="add",
        )
    )

    assert warnings == ()
    assert calls == [
        (
            "POST",
            "/repos/Soju06/codex-lb/issues/714/labels",
            {"labels": ["needs rebase"]},
        )
    ]


def test_apply_decision_removes_stale_needs_rebase_label(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    calls: list[tuple[str, str, Any | None]] = []

    def capture_write(path: str, *, method: str = "GET", input_json: Any | None = None) -> None:
        calls.append((method, path, input_json))

    monkeypatch.setattr(module, "gh_api", capture_write)

    warnings = module.apply_decision(
        decision(
            module,
            ok_action="keep",
            has_needs_rebase_label=True,
            wants_needs_rebase_label=False,
            needs_rebase_action="remove",
        )
    )

    assert warnings == ()
    assert calls == [
        (
            "DELETE",
            "/repos/Soju06/codex-lb/issues/714/labels/needs%20rebase",
            None,
        )
    ]


def test_classify_check_state_uses_latest_run_for_duplicate_check_names() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "failure",
            "completed_at": "2026-06-11T07:40:59Z",
        },
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-06-11T07:45:35Z",
        },
        {
            "name": "Type check (ty)",
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-06-11T07:41:20Z",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required", "Type check (ty)"}),
        )
        == "success"
    )


def test_classify_check_state_keeps_latest_pending_duplicate_pending() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-06-11T07:40:59Z",
        },
        {
            "name": "CI Required",
            "status": "in_progress",
            "conclusion": None,
            "started_at": "2026-06-11T07:45:35Z",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required"}),
        )
        == "pending"
    )


def test_classify_check_state_ignores_stale_duplicate_that_finishes_late() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-06-11T07:40:59Z",
            "completed_at": "2026-06-11T07:50:00Z",
        },
        {
            "name": "CI Required",
            "status": "in_progress",
            "conclusion": None,
            "started_at": "2026-06-11T07:45:35Z",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required"}),
        )
        == "pending"
    )


def test_classify_check_state_ignores_unique_failure_from_superseded_ci_run() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "Tests (pytest, ${{ matrix.slice.name }})",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-07-10T06:00:37Z",
            "completed_at": "2026-07-10T06:00:37Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/100/job/1",
            "_github_actions_workflow_id": "ci",
        },
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-07-10T06:00:38Z",
            "completed_at": "2026-07-10T06:00:41Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/100/job/2",
            "_github_actions_workflow_id": "ci",
        },
        {
            "name": "Tests (pytest, unit)",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-10T06:01:00Z",
            "completed_at": "2026-07-10T06:05:00Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/3",
            "_github_actions_workflow_id": "ci",
        },
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-10T06:09:01Z",
            "completed_at": "2026-07-10T06:09:05Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/4",
            "_github_actions_workflow_id": "ci",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required", "Tests (pytest, unit)"}),
        )
        == "success"
    )


def test_classify_check_state_keeps_optional_failure_from_authoritative_ci_run() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "optional security scan",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-07-10T06:09:00Z",
            "completed_at": "2026-07-10T06:09:04Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/3",
            "_github_actions_workflow_id": "ci",
        },
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-10T06:09:01Z",
            "completed_at": "2026-07-10T06:09:05Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/4",
            "_github_actions_workflow_id": "ci",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required"}),
        )
        == "failure"
    )


def test_classify_check_state_keeps_newer_same_workflow_run_pending_before_required_job_exists() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-10T06:09:01Z",
            "completed_at": "2026-07-10T06:09:05Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/4",
            "_github_actions_workflow_id": "ci",
            "_github_actions_run_created_at": "2026-07-10T06:00:43Z",
        },
        {
            "name": "Detect changes",
            "status": "in_progress",
            "conclusion": None,
            "started_at": "2026-07-10T06:50:20Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/300/job/1",
            "_github_actions_workflow_id": "ci",
            "_github_actions_run_created_at": "2026-07-10T06:50:20Z",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required"}),
        )
        == "pending"
    )


def test_classify_check_state_keeps_manual_rerun_of_older_run_pending() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-10T06:50:20Z",
            "completed_at": "2026-07-10T06:59:05Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/4",
            "_github_actions_workflow_id": "ci",
            "_github_actions_run_created_at": "2026-07-10T06:50:00Z",
            "_github_actions_run_started_at": "2026-07-10T06:50:00Z",
        },
        {
            "name": "Detect changes",
            "status": "in_progress",
            "conclusion": None,
            "started_at": "2026-07-10T07:10:20Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/100/job/1",
            "_github_actions_workflow_id": "ci",
            "_github_actions_run_created_at": "2026-07-10T06:00:00Z",
            "_github_actions_run_started_at": "2026-07-10T07:10:00Z",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required"}),
        )
        == "pending"
    )


def test_classify_check_state_keeps_failure_from_manual_rerun_of_older_run() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-10T06:50:20Z",
            "completed_at": "2026-07-10T06:59:05Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/4",
            "_github_actions_workflow_id": "ci",
            "_github_actions_run_created_at": "2026-07-10T06:50:00Z",
            "_github_actions_run_started_at": "2026-07-10T06:50:00Z",
        },
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-07-10T07:10:20Z",
            "completed_at": "2026-07-10T07:15:05Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/100/job/4",
            "_github_actions_workflow_id": "ci",
            "_github_actions_run_created_at": "2026-07-10T06:00:00Z",
            "_github_actions_run_started_at": "2026-07-10T07:10:00Z",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required"}),
        )
        == "failure"
    )


def test_classify_check_state_keeps_failure_from_independent_workflow_run() -> None:
    module = load_sync_module()

    check_runs = [
        {
            "name": "independent security scan",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-07-10T06:08:00Z",
            "completed_at": "2026-07-10T06:08:30Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/300/job/1",
            "_github_actions_workflow_id": "security",
        },
        {
            "name": "CI Required",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-10T06:09:01Z",
            "completed_at": "2026-07-10T06:09:05Z",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/4",
            "_github_actions_workflow_id": "ci",
        },
    ]

    assert (
        module.classify_check_state(
            check_runs,
            {"statuses": []},
            required_check_names=frozenset({"CI Required"}),
        )
        == "failure"
    )


def test_annotate_github_actions_workflow_ids_is_conservative_when_metadata_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_sync_module()
    check_runs = [
        {
            "name": "CI Required",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/200/job/4",
        },
        {
            "name": "independent scan",
            "details_url": "https://github.com/Soju06/codex-lb/actions/runs/300/job/1",
        },
    ]

    def workflow_run(path: str) -> dict[str, int | str]:
        if path.endswith("/200"):
            return {
                "workflow_id": 10,
                "created_at": "2026-07-10T06:00:43Z",
                "run_started_at": "2026-07-10T07:10:00Z",
            }
        raise module.GhError("metadata unavailable")

    monkeypatch.setattr(module, "gh_api", workflow_run)

    annotated = module.annotate_github_actions_workflow_ids("Soju06/codex-lb", check_runs)

    assert annotated[0]["_github_actions_workflow_id"] == "10"
    assert annotated[0]["_github_actions_run_started_at"] == "2026-07-10T07:10:00Z"
    assert "_github_actions_workflow_id" not in annotated[1]


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


def test_codex_usage_backoff_blocks_recent_limit_for_same_sender() -> None:
    module = load_sync_module()
    backoff = module.CodexReviewUsageBackoff(
        request_author="Komzpa",
        allowed_authors={"chatgpt-codex-connector"},
        window=module.timedelta(hours=24),
        now=module.datetime.fromisoformat("2026-07-31T16:00:00+00:00"),
    )

    backoff.observe(
        [
            codex_review_request("Komzpa", "2026-07-31T15:00:00Z"),
            codex_issue_comment("You've reached your Codex usage limits.", "2026-07-31T15:01:00Z"),
        ]
    )

    assert backoff.is_limited() is True


def test_codex_usage_backoff_allows_after_newer_normal_reply() -> None:
    module = load_sync_module()
    backoff = module.CodexReviewUsageBackoff(
        request_author="Komzpa",
        allowed_authors={"chatgpt-codex-connector"},
        window=module.timedelta(hours=24),
        now=module.datetime.fromisoformat("2026-07-31T16:00:00+00:00"),
    )

    backoff.observe(
        [
            codex_review_request("Komzpa", "2026-07-31T14:00:00Z"),
            codex_issue_comment("You've reached your Codex usage limits.", "2026-07-31T14:01:00Z"),
            codex_review_request("Komzpa", "2026-07-31T15:00:00Z"),
            codex_issue_comment("Codex Review: Didn't find any major issues.", "2026-07-31T15:02:00Z"),
        ]
    )

    assert backoff.is_limited() is False


def test_codex_usage_backoff_keeps_accounts_independent() -> None:
    module = load_sync_module()
    backoff = module.CodexReviewUsageBackoff(
        request_author="Komzpa",
        allowed_authors={"chatgpt-codex-connector"},
        window=module.timedelta(hours=24),
        now=module.datetime.fromisoformat("2026-07-31T16:00:00+00:00"),
    )

    backoff.observe(
        [
            codex_review_request("Komzpa", "2026-07-31T14:00:00Z"),
            codex_issue_comment("You've reached your Codex usage limits.", "2026-07-31T14:01:00Z"),
            codex_review_request("OtherUser", "2026-07-31T15:00:00Z"),
            codex_issue_comment("Codex Review: Didn't find any major issues.", "2026-07-31T15:02:00Z"),
        ]
    )

    assert backoff.is_limited() is True


@pytest.mark.parametrize(
    "body",
    [
        "You have reached your Codex usage limits for code reviews. "
        "You can see your limits in the [Codex usage dashboard](https://chatgpt.com/codex/settings/usage).",
        "  \nYou have reached your Codex usage limits for code reviews.",
        "You've reached your Codex usage limits.",
    ],
)
def test_usage_limit_body_matches_real_quota_envelope(body: str) -> None:
    module = load_sync_module()

    assert module.is_codex_usage_limit_body(body) is True


@pytest.mark.parametrize(
    "body",
    [
        "**[P1]** The unanchored `usage limit` pattern also matches reviews discussing usage limits.",
        "Codex Review: the backoff should latch on a Codex usage limit reply. Didn't find any major issues.",
        "This PR adds a usage-limit backoff. You have reached your Codex usage limits is the trigger phrase.",
        None,
        "",
    ],
)
def test_usage_limit_body_ignores_reviews_discussing_usage_limits(body: object) -> None:
    module = load_sync_module()

    assert module.is_codex_usage_limit_body(body) is False


def codex_review_request_with_reaction(
    author: str,
    created_at: str,
    *,
    reaction_user: str,
    reaction_content: str,
    reaction_created_at: str,
) -> dict[str, Any]:
    request = codex_review_request(author, created_at)
    request["reactions"] = {
        "nodes": [
            {
                "content": reaction_content,
                "createdAt": reaction_created_at,
                "user": {"login": reaction_user},
            }
        ]
    }
    return request


def test_codex_usage_backoff_unlatches_on_newer_clean_reaction() -> None:
    module = load_sync_module()
    backoff = module.CodexReviewUsageBackoff(
        request_author="Komzpa",
        allowed_authors={"chatgpt-codex-connector"},
        window=module.timedelta(hours=24),
        now=module.datetime.fromisoformat("2026-07-31T16:00:00+00:00"),
    )

    backoff.observe(
        [
            codex_review_request("Komzpa", "2026-07-31T14:00:00Z"),
            codex_issue_comment("You've reached your Codex usage limits.", "2026-07-31T14:01:00Z"),
            codex_review_request_with_reaction(
                "Komzpa",
                "2026-07-31T15:00:00Z",
                reaction_user="chatgpt-codex-connector",
                reaction_content="THUMBS_UP",
                reaction_created_at="2026-07-31T15:05:00Z",
            ),
        ]
    )

    assert backoff.is_limited() is False


def test_codex_usage_backoff_ignores_reactions_from_non_codex_users() -> None:
    module = load_sync_module()
    backoff = module.CodexReviewUsageBackoff(
        request_author="Komzpa",
        allowed_authors={"chatgpt-codex-connector"},
        window=module.timedelta(hours=24),
        now=module.datetime.fromisoformat("2026-07-31T16:00:00+00:00"),
    )

    backoff.observe(
        [
            codex_review_request("Komzpa", "2026-07-31T14:00:00Z"),
            codex_issue_comment("You've reached your Codex usage limits.", "2026-07-31T14:01:00Z"),
            codex_review_request_with_reaction(
                "Komzpa",
                "2026-07-31T15:00:00Z",
                reaction_user="SomeoneElse",
                reaction_content="THUMBS_UP",
                reaction_created_at="2026-07-31T15:05:00Z",
            ),
        ]
    )

    assert backoff.is_limited() is True


def test_codex_usage_backoff_ignores_clean_reaction_older_than_limit() -> None:
    module = load_sync_module()
    backoff = module.CodexReviewUsageBackoff(
        request_author="Komzpa",
        allowed_authors={"chatgpt-codex-connector"},
        window=module.timedelta(hours=24),
        now=module.datetime.fromisoformat("2026-07-31T16:00:00+00:00"),
    )

    backoff.observe(
        [
            codex_review_request_with_reaction(
                "Komzpa",
                "2026-07-31T13:00:00Z",
                reaction_user="chatgpt-codex-connector",
                reaction_content="THUMBS_UP",
                reaction_created_at="2026-07-31T13:05:00Z",
            ),
            codex_review_request("Komzpa", "2026-07-31T14:00:00Z"),
            codex_issue_comment("You've reached your Codex usage limits.", "2026-07-31T14:01:00Z"),
        ]
    )

    assert backoff.is_limited() is True


def test_resolve_codex_request_sender_prefers_app_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    monkeypatch.setenv("GH_APP_SLUG", "codex-label-sync")

    def fail_viewer_login() -> str:
        raise AssertionError("GET /user must not be called when GH_APP_SLUG is set")

    monkeypatch.setattr(module, "current_viewer_login", fail_viewer_login)

    assert module.resolve_codex_request_sender() == "codex-label-sync[bot]"


def test_resolve_codex_request_sender_keeps_explicit_bot_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    monkeypatch.setenv("GH_APP_SLUG", "codex-label-sync[bot]")

    assert module.resolve_codex_request_sender() == "codex-label-sync[bot]"


def test_resolve_codex_request_sender_falls_back_to_viewer_login(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "current_viewer_login", lambda: "Komzpa")

    assert module.resolve_codex_request_sender() == "Komzpa"


def test_resolve_codex_request_sender_returns_none_when_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()
    monkeypatch.delenv("GH_APP_SLUG", raising=False)

    def fail_viewer_login() -> str:
        raise module.GhError("gh api /user: HTTP 403 (installation token)")

    monkeypatch.setattr(module, "current_viewer_login", fail_viewer_login)

    assert module.resolve_codex_request_sender() is None
    assert "cannot resolve @codex review sender" in capsys.readouterr().err


def recent_timestamp(module: ModuleType, *, minutes_ago: int) -> str:
    moment = module.datetime.now(module.UTC) - module.timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_main_stops_codex_review_triggers_after_probe_hits_usage_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710, 714])
    monkeypatch.setattr(module, "current_viewer_login", lambda: "Komzpa")
    monkeypatch.setattr(module, "recent_issue_comment_timelines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    def fake_decide_pr(_repo: str, number: int, **kwargs: Any) -> Any:
        observer = kwargs.get("timeline_observer")
        if observer is not None:
            observer(
                number,
                [
                    {
                        "__typename": "PullRequestCommit",
                        "commit": {"oid": "a" * 40},
                        "committedDate": recent_timestamp(module, minutes_ago=70),
                    }
                ],
            )
        return decision(module, number=number, trigger_codex_review=True, ok_action="keep", checks_state="success")

    posted: list[int] = []

    def fake_trigger(decision: Any, **_kwargs: Any) -> tuple[str, ...]:
        posted.append(decision.number)
        return ()

    def fake_timeline(_repo: str, _number: int) -> tuple[str, list[dict[str, Any]]]:
        return (
            "a" * 40,
            [
                codex_review_request("Komzpa", recent_timestamp(module, minutes_ago=2)),
                codex_issue_comment(
                    "You've reached your Codex usage limits.",
                    recent_timestamp(module, minutes_ago=1),
                ),
            ],
        )

    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)
    monkeypatch.setattr(module, "trigger_codex_review", fake_trigger)
    monkeypatch.setattr(module, "pr_timeline_evidence", fake_timeline)

    result = module.main(
        [
            "--repo",
            "Soju06/codex-lb",
            "--all-open",
            "--apply",
            "--codex-review-response-wait-seconds",
            "0",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert posted == [710]
    apply_lines = [line for line in captured.out.splitlines() if line.startswith("apply ")]
    assert len(apply_lines) == 2
    assert apply_lines[0].startswith("apply Soju06/codex-lb#710: ")
    assert "trigger_codex=True" in apply_lines[0]
    assert apply_lines[1].startswith("apply Soju06/codex-lb#714: ")
    assert "trigger_codex=False" in apply_lines[1]
    assert "recent Codex usage-limit reply" in captured.out


def test_main_skips_probe_after_normal_codex_response_observed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710, 714])
    monkeypatch.setattr(module, "current_viewer_login", lambda: "Komzpa")
    monkeypatch.setattr(module, "recent_issue_comment_timelines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    def fake_decide_pr(_repo: str, number: int, **kwargs: Any) -> Any:
        observer = kwargs.get("timeline_observer")
        if observer is not None:
            observer(
                number,
                [
                    codex_review_request("Komzpa", recent_timestamp(module, minutes_ago=30)),
                    codex_issue_comment(
                        "Codex Review: Didn't find any major issues.",
                        recent_timestamp(module, minutes_ago=29),
                    ),
                ],
            )
        return decision(module, number=number, trigger_codex_review=True, ok_action="keep", checks_state="success")

    posted: list[int] = []

    def fake_trigger(decision: Any, **_kwargs: Any) -> tuple[str, ...]:
        posted.append(decision.number)
        return ()

    probe_calls: list[int] = []

    def fake_timeline(_repo: str, number: int) -> tuple[str, list[dict[str, Any]]]:
        probe_calls.append(number)
        return ("a" * 40, [])

    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)
    monkeypatch.setattr(module, "trigger_codex_review", fake_trigger)
    monkeypatch.setattr(module, "pr_timeline_evidence", fake_timeline)

    result = module.main(
        [
            "--repo",
            "Soju06/codex-lb",
            "--all-open",
            "--apply",
            "--codex-review-response-wait-seconds",
            "0",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert posted == [710, 714]
    assert probe_calls == []
    assert "apply Soju06/codex-lb#710: " in captured.out
    assert "apply Soju06/codex-lb#714: " in captured.out


def test_main_continues_label_sync_when_sender_is_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710, 714])
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    def fail_viewer_login() -> str:
        raise module.GhError("gh api /user: HTTP 403 (installation token)")

    monkeypatch.setattr(module, "current_viewer_login", fail_viewer_login)
    monkeypatch.setattr(module, "recent_issue_comment_timelines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "decide_pr",
        lambda _repo, number, **_kwargs: decision(
            module, number=number, trigger_codex_review=True, checks_state="success"
        ),
    )

    applied: list[int] = []
    monkeypatch.setattr(
        module,
        "apply_decision",
        lambda applied_decision, **_kwargs: (applied.append(applied_decision.number), ())[1],
    )
    posted: list[int] = []
    monkeypatch.setattr(
        module,
        "trigger_codex_review",
        lambda request_decision, **_kwargs: (posted.append(request_decision.number), ())[1],
    )

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply"])

    captured = capsys.readouterr()
    assert result == 0
    assert applied == [710, 714]
    assert posted == []
    assert "cannot determine @codex review sender; skipping review triggers" in captured.err
    assert captured.out.count("sender could not be resolved") == 2
    assert captured.out.count("trigger_codex=False") == 2


def test_main_skips_apply_when_head_moved_after_classification(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710, 714])
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    calls_by_number: dict[int, int] = {}

    def fake_decide_pr(_repo: str, number: int, **_kwargs: Any) -> Any:
        calls_by_number[number] = calls_by_number.get(number, 0) + 1
        # The classification pass sees head a...a; by the time #710 is
        # re-classified in the apply loop its head has moved to b...b.
        if number == 710 and calls_by_number[number] > 1:
            return decision(module, number=number, head_sha="b" * 40)
        return decision(module, number=number)

    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)

    applied: list[int] = []
    monkeypatch.setattr(
        module,
        "apply_decision",
        lambda applied_decision, **_kwargs: (applied.append(applied_decision.number), ())[1],
    )

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply"])

    captured = capsys.readouterr()
    assert result == 0
    assert applied == [714]
    assert calls_by_number == {710: 2, 714: 2}
    assert "Soju06/codex-lb#710: head moved from" in captured.err
    assert "skipping stale decision" in captured.err
    assert "apply Soju06/codex-lb#710" not in captured.out
    assert "apply Soju06/codex-lb#714" in captured.out


def test_main_applies_freshly_reclassified_decision_for_same_head(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [714])
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    calls: list[int] = []

    def fake_decide_pr(_repo: str, number: int, **_kwargs: Any) -> Any:
        calls.append(number)
        if len(calls) == 1:
            return decision(
                module,
                number=number,
                has_ok_label=False,
                wants_ok_label=True,
                ok_action="add",
                checks_state="success",
            )
        # Same head, but by apply time Codex raised a new finding.
        return decision(
            module,
            number=number,
            has_ok_label=False,
            wants_ok_label=False,
            ok_action="keep",
            wants_needs_work_label=True,
            needs_work_action="add",
            review_state="needs_work",
        )

    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)

    applied_actions: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module,
        "apply_decision",
        lambda applied_decision, **_kwargs: (
            applied_actions.append((applied_decision.ok_action, applied_decision.needs_work_action)),
            (),
        )[1],
    )

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply"])

    captured = capsys.readouterr()
    assert result == 0
    assert applied_actions == [("keep", "add")]
    assert "review=needs_work" in captured.out


def test_main_usage_backoff_state_persists_across_repos(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710])
    monkeypatch.setattr(module, "current_viewer_login", lambda: "Komzpa")
    monkeypatch.setattr(module, "recent_issue_comment_timelines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    def fake_decide_pr(repo: str, number: int, **kwargs: Any) -> Any:
        observer = kwargs.get("timeline_observer")
        if observer is not None:
            timeline: list[dict[str, Any]] = [
                {
                    "__typename": "PullRequestCommit",
                    "commit": {"oid": "a" * 40},
                    "committedDate": recent_timestamp(module, minutes_ago=70),
                }
            ]
            if repo == "Soju06/codex-lb":
                timeline.extend(
                    [
                        codex_review_request("Komzpa", recent_timestamp(module, minutes_ago=30)),
                        codex_issue_comment(
                            "You have reached your Codex usage limits for code reviews.",
                            recent_timestamp(module, minutes_ago=29),
                        ),
                    ]
                )
            observer(number, timeline)
        return decision(module, repo=repo, number=number, trigger_codex_review=True, checks_state="success")

    posted: list[str] = []
    monkeypatch.setattr(
        module,
        "trigger_codex_review",
        lambda request_decision, **_kwargs: (posted.append(request_decision.repo), ())[1],
    )
    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)

    result = module.main(
        [
            "--repo",
            "Soju06/codex-lb",
            "--repo",
            "Soju06/other-repo",
            "--all-open",
            "--apply",
            "--codex-review-response-wait-seconds",
            "0",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert posted == []
    assert "apply Soju06/codex-lb#710" in captured.out
    assert "apply Soju06/other-repo#710" in captured.out
    assert "request Codex review on Soju06/other-repo#710: skipped" in captured.out
    assert "recent Codex usage-limit reply" in captured.out


def test_main_usage_backoff_counts_evidence_from_non_triggering_repo(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710])
    monkeypatch.setattr(module, "current_viewer_login", lambda: "Komzpa")
    monkeypatch.setattr(module, "recent_issue_comment_timelines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    def fake_decide_pr(repo: str, number: int, **kwargs: Any) -> Any:
        observer = kwargs.get("timeline_observer")
        if observer is not None and repo == "Soju06/codex-lb":
            # The first repo has quota evidence but no trigger of its own.
            observer(
                number,
                [
                    codex_review_request("Komzpa", recent_timestamp(module, minutes_ago=30)),
                    codex_issue_comment(
                        "You have reached your Codex usage limits for code reviews.",
                        recent_timestamp(module, minutes_ago=29),
                    ),
                ],
            )
        elif observer is not None:
            observer(number, [])
        trigger = repo == "Soju06/other-repo"
        return decision(
            module,
            repo=repo,
            number=number,
            ok_action="keep",
            has_ok_label=False,
            trigger_codex_review=trigger,
            checks_state="success",
        )

    posted: list[str] = []
    monkeypatch.setattr(
        module,
        "trigger_codex_review",
        lambda request_decision, **_kwargs: (posted.append(request_decision.repo), ())[1],
    )
    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)

    result = module.main(
        [
            "--repo",
            "Soju06/codex-lb",
            "--repo",
            "Soju06/other-repo",
            "--all-open",
            "--apply",
            "--codex-review-response-wait-seconds",
            "0",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert posted == []
    assert "request Codex review on Soju06/other-repo#710: skipped" in captured.out
    assert "recent Codex usage-limit reply" in captured.out


def test_main_stops_codex_review_triggers_after_fallback_token_activates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.setenv("GH_APP_SLUG", "codex-label-sync")
    monkeypatch.setattr(module, "_fallback_token_active", True)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [714])
    monkeypatch.setattr(module, "current_viewer_login", lambda: "fallback-user")
    monkeypatch.setattr(module, "recent_issue_comment_timelines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        module,
        "decide_pr",
        lambda _repo, number, **_kwargs: decision(
            module,
            number=number,
            ok_action="keep",
            has_ok_label=False,
            trigger_codex_review=True,
            checks_state="success",
        ),
    )

    posted: list[int] = []
    monkeypatch.setattr(
        module,
        "trigger_codex_review",
        lambda request_decision, **_kwargs: (posted.append(request_decision.number), ())[1],
    )

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply"])

    captured = capsys.readouterr()
    assert result == 0
    assert posted == []
    assert "switched to GH_FALLBACK_TOKEN" in captured.out
    assert "trigger_codex=False" in captured.out


def test_resolve_codex_request_sender_ignores_app_slug_after_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    monkeypatch.setenv("GH_APP_SLUG", "codex-label-sync")
    monkeypatch.setattr(module, "_fallback_token_active", True)
    monkeypatch.setattr(module, "current_viewer_login", lambda: "fallback-user")

    assert module.resolve_codex_request_sender() == "fallback-user"


def test_recent_issue_comment_timelines_groups_by_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()

    comments = [
        {
            "body": "@codex review",
            "issue_url": "https://api.github.test/repos/Soju06/codex-lb/issues/700",
            "created_at": "2026-07-31T14:00:00Z",
            "html_url": "https://github.test/pull/700#issuecomment-1",
            "user": {"login": "Komzpa"},
        },
        {
            "body": "unrelated comment on another issue",
            "issue_url": "https://api.github.test/repos/Soju06/codex-lb/issues/701",
            "created_at": "2026-07-31T14:00:30Z",
            "html_url": "https://github.test/pull/701#issuecomment-2",
            "user": {"login": "someone"},
        },
        {
            "body": "You have reached your Codex usage limits for code reviews.",
            "issue_url": "https://api.github.test/repos/Soju06/codex-lb/issues/700",
            "created_at": "2026-07-31T14:01:00Z",
            "html_url": "https://github.test/pull/700#issuecomment-3",
            "user": {"login": "chatgpt-codex-connector"},
        },
    ]
    paths: list[str] = []

    def fake_paged_api(path: str) -> list[dict[str, Any]]:
        paths.append(path)
        return comments

    monkeypatch.setattr(module, "paged_api", fake_paged_api)

    timelines = module.recent_issue_comment_timelines(
        "Soju06/codex-lb",
        since=module.datetime.fromisoformat("2026-07-30T16:00:00+00:00"),
    )

    assert len(paths) == 1
    assert paths[0].startswith("/repos/Soju06/codex-lb/issues/comments?since=2026-07-30T16")
    assert len(timelines) == 2
    grouped = {timeline[0]["url"].split("#")[0]: timeline for timeline in timelines}
    pr_700 = grouped["https://github.test/pull/700"]
    assert [node["bodyText"] for node in pr_700] == [
        "@codex review",
        "You have reached your Codex usage limits for code reviews.",
    ]
    assert all(node["__typename"] == "IssueComment" for node in pr_700)

    backoff = module.CodexReviewUsageBackoff(
        request_author="Komzpa",
        allowed_authors={"chatgpt-codex-connector"},
        window=module.timedelta(hours=24),
        now=module.datetime.fromisoformat("2026-07-31T16:00:00+00:00"),
    )
    for timeline in timelines:
        backoff.observe(timeline)
    assert backoff.is_limited() is True


def test_main_gathers_repo_wide_quota_evidence_for_single_pr_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "current_viewer_login", lambda: "Komzpa")
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        module,
        "decide_pr",
        lambda _repo, number, **_kwargs: decision(
            module,
            number=number,
            ok_action="keep",
            has_ok_label=False,
            trigger_codex_review=True,
            checks_state="success",
        ),
    )
    # Quota evidence lives on another (already closed) PR of the repo.
    monkeypatch.setattr(
        module,
        "recent_issue_comment_timelines",
        lambda _repo, **_kwargs: [
            [
                codex_review_request("Komzpa", recent_timestamp(module, minutes_ago=30)),
                codex_issue_comment(
                    "You have reached your Codex usage limits for code reviews.",
                    recent_timestamp(module, minutes_ago=29),
                ),
            ]
        ],
    )

    posted: list[int] = []
    monkeypatch.setattr(
        module,
        "trigger_codex_review",
        lambda request_decision, **_kwargs: (posted.append(request_decision.number), ())[1],
    )

    result = module.main(["--repo", "Soju06/codex-lb", "--pr", "714", "--apply"])

    captured = capsys.readouterr()
    assert result == 0
    assert posted == []
    assert "request Codex review on Soju06/codex-lb#714: skipped" in captured.out
    assert "recent Codex usage-limit reply" in captured.out


def test_main_observes_quota_evidence_from_apply_time_reclassification(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [710, 714])
    monkeypatch.setattr(module, "current_viewer_login", lambda: "Komzpa")
    monkeypatch.setattr(module, "recent_issue_comment_timelines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    calls_by_number: dict[int, int] = {}

    def fake_decide_pr(_repo: str, number: int, **kwargs: Any) -> Any:
        calls_by_number[number] = calls_by_number.get(number, 0) + 1
        reclassification = calls_by_number[number] > 1
        observer = kwargs.get("timeline_observer")
        if observer is not None:
            timeline: list[dict[str, Any]] = [
                {
                    "__typename": "PullRequestCommit",
                    "commit": {"oid": "a" * 40},
                    "committedDate": recent_timestamp(module, minutes_ago=70),
                }
            ]
            if reclassification and number == 710:
                # A quota reply arrived between bulk classification and apply.
                timeline.extend(
                    [
                        codex_review_request("Komzpa", recent_timestamp(module, minutes_ago=3)),
                        codex_issue_comment(
                            "You have reached your Codex usage limits for code reviews.",
                            recent_timestamp(module, minutes_ago=2),
                        ),
                    ]
                )
            observer(number, timeline)
        trigger = not (reclassification and number == 710)
        return decision(
            module,
            number=number,
            ok_action="keep",
            has_ok_label=False,
            trigger_codex_review=trigger,
            checks_state="success",
        )

    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)

    posted: list[int] = []
    monkeypatch.setattr(
        module,
        "trigger_codex_review",
        lambda request_decision, **_kwargs: (posted.append(request_decision.number), ())[1],
    )

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply"])

    captured = capsys.readouterr()
    assert result == 0
    assert posted == []
    assert "request Codex review on Soju06/codex-lb#714: skipped" in captured.out
    assert "recent Codex usage-limit reply" in captured.out


def test_main_tolerates_apply_time_reclassification_read_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [714])
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    calls: list[int] = []

    def fake_decide_pr(_repo: str, number: int, **_kwargs: Any) -> Any:
        calls.append(number)
        if len(calls) > 1:
            raise module.GhError("gh: HTTP 502")
        return decision(module, number=number)

    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)

    applied: list[int] = []
    monkeypatch.setattr(
        module,
        "apply_decision",
        lambda applied_decision, **_kwargs: (applied.append(applied_decision.number), ())[1],
    )

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply", "--tolerate-read-errors"])

    captured = capsys.readouterr()
    assert result == 0
    assert applied == []
    assert "Soju06/codex-lb#714: apply-time reclassification failed" in captured.err


def test_main_fails_apply_time_reclassification_read_errors_without_tolerance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_sync_module()

    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [714])
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())

    calls: list[int] = []

    def fake_decide_pr(_repo: str, number: int, **_kwargs: Any) -> Any:
        calls.append(number)
        if len(calls) > 1:
            raise module.GhError("gh: HTTP 502")
        return decision(module, number=number)

    monkeypatch.setattr(module, "decide_pr", fake_decide_pr)
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())

    assert module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply"]) == 1


def test_main_skips_probe_when_trigger_post_was_denied(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_sync_module()

    monkeypatch.delenv("GH_APP_SLUG", raising=False)
    monkeypatch.setattr(module, "ensure_label", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "list_open_pr_numbers", lambda _repo: [714])
    monkeypatch.setattr(module, "current_viewer_login", lambda: "Komzpa")
    monkeypatch.setattr(module, "recent_issue_comment_timelines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "apply_decision", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(module, "approve_workflow_runs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        module,
        "decide_pr",
        lambda _repo, number, **_kwargs: decision(
            module,
            number=number,
            ok_action="keep",
            has_ok_label=False,
            trigger_codex_review=True,
            checks_state="success",
        ),
    )
    monkeypatch.setattr(
        module,
        "trigger_codex_review",
        lambda request_decision, **_kwargs: (
            f"request Codex review on {request_decision.repo}#{request_decision.number}: "
            "skipped because the GitHub token cannot write this resource",
        ),
    )

    probe_calls: list[int] = []
    monkeypatch.setattr(
        module,
        "pr_timeline_evidence",
        lambda _repo, number: (probe_calls.append(number), ("a" * 40, []))[1],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    result = module.main(["--repo", "Soju06/codex-lb", "--all-open", "--apply"])

    captured = capsys.readouterr()
    assert result == 0
    assert probe_calls == []
    assert sleeps == []
    assert "write_warning=request Codex review on Soju06/codex-lb#714" in captured.out


def test_workflow_prefers_privileged_token_and_enables_tolerant_apply() -> None:
    workflow = Path(".github/workflows/codex-review-labels.yml").read_text(encoding="utf-8")

    assert "secrets.CODEX_LABEL_SYNC_TOKEN || secrets.RELEASE_PLEASE_TOKEN || github.token" in workflow
    app_slug_env = "GH_APP_SLUG: ${{ steps.app-token.outputs.token && steps.app-token.outputs.app-slug || '' }}"
    assert workflow.count(app_slug_env) == 2
    assert "pull_request_review_thread:" not in workflow
    assert "github.event_name == 'pull_request_review_thread'" not in workflow
    assert 'cron: "*/15 * * * *"' in workflow
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


def test_pull_review_comment_nodes_uses_original_commit_or_head_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    head_sha = "a" * 40
    old_sha = "b" * 40
    comment_data = [
        {
            "body": "reanchored current-head inline review",
            "commit_id": head_sha,
            "original_commit_id": old_sha,
            "pull_request_review_id": 1,
            "created_at": "2026-06-11T00:00:00Z",
            "html_url": "https://github.com/Soju06/codex-lb/pull/714#discussion_r1",
            "user": {"login": "openai-codex"},
        },
        {
            "body": f"stale but mentions head commit {head_sha[:12]}",
            "commit_id": head_sha,
            "original_commit_id": old_sha,
            "pull_request_review_id": 2,
            "created_at": "2026-06-11T00:00:00Z",
            "html_url": "https://github.com/Soju06/codex-lb/pull/714#discussion_r2",
            "user": {"login": "openai-codex"},
        },
        {
            "body": "actual current-head inline review",
            "commit_id": old_sha,
            "original_commit_id": head_sha,
            "pull_request_review_id": 3,
            "created_at": "2026-06-11T00:00:00Z",
            "html_url": "https://github.com/Soju06/codex-lb/pull/714#discussion_r3",
            "user": {"login": "openai-codex"},
        },
        {
            "body": "older unrelated comment",
            "commit_id": old_sha,
            "original_commit_id": old_sha,
            "pull_request_review_id": 4,
            "created_at": "2026-06-11T00:00:00Z",
            "html_url": "https://github.com/Soju06/codex-lb/pull/714#discussion_r4",
            "user": {"login": "openai-codex"},
        },
    ]

    monkeypatch.setattr(module, "paged_api", lambda _path: comment_data)
    monkeypatch.setattr(module, "unresolved_review_comment_urls", lambda *_args: set())

    nodes = module.pull_review_comment_nodes("Soju06/codex-lb", 714, head_sha=head_sha)

    assert [node.get("commit", {}).get("oid") for node in nodes] == [head_sha, head_sha, head_sha]
    assert [node.get("pullRequestReviewDatabaseId") for node in nodes] == [None, None, 3]


def test_head_mentioned_fallback_comment_keeps_timeline_chronology() -> None:
    module = load_sync_module()
    head_sha = "a" * 40
    review_id = 2
    timeline_nodes = [
        {
            "__typename": "PullRequestCommit",
            "commit": {"oid": head_sha},
            "committedDate": "2026-06-11T06:30:00Z",
        },
        {
            "__typename": "PullRequestReview",
            "databaseId": review_id,
            "author": {"login": "openai-codex"},
            "bodyText": "Reviewed older commit.",
            "submittedAt": "2026-06-11T06:32:00Z",
            "commit": {"oid": "b" * 40},
        },
        {
            "__typename": "IssueComment",
            "author": {"login": "openai-codex"},
            "bodyText": "Codex Review: Didn't find any major issues.",
            "createdAt": "2026-06-11T06:40:00Z",
        },
    ]
    comment_nodes = [
        {
            "__typename": "PullRequestReviewComment",
            "author": {"login": "openai-codex"},
            "bodyText": f"**[P2]** stale finding mentioning {head_sha[:12]}",
            "createdAt": "2026-06-11T06:34:00Z",
            "commit": {"oid": head_sha},
            "pullRequestReviewDatabaseId": None,
        }
    ]

    merged = module.merge_review_comment_nodes(timeline_nodes, comment_nodes)
    assert [node["__typename"] for node in merged] == [
        "PullRequestCommit",
        "PullRequestReview",
        "PullRequestReviewComment",
        "IssueComment",
    ]

    state, node = module.find_current_head_codex_review_state(
        merged,
        head_sha=head_sha,
        allowed_authors={"openai-codex"},
    )

    assert state == "clean"
    assert node is timeline_nodes[-1]


def test_unresolved_codex_threads_filter_to_current_head(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    head_sha = "a" * 40
    old_sha = "b" * 40

    pages = [
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "openai-codex"},
                                                "body": "**[P1]** reanchored current-head finding",
                                                "url": "https://example.invalid/reanchored-current",
                                                "commit": {"oid": head_sha},
                                                "originalCommit": {"oid": old_sha},
                                            }
                                        ]
                                    },
                                },
                                {
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "openai-codex"},
                                                "body": "**[P1]** current finding",
                                                "url": "https://example.invalid/current",
                                                "commit": {"oid": head_sha},
                                                "originalCommit": {"oid": head_sha},
                                            }
                                        ]
                                    },
                                },
                                {
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "openai-codex"},
                                                "body": f"**[P2]** stale fallback for {head_sha[:12]}",
                                                "url": "https://example.invalid/fallback",
                                                "commit": {"oid": old_sha},
                                                "originalCommit": {"oid": old_sha},
                                            }
                                        ]
                                    },
                                },
                                {
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "openai-codex"},
                                                "body": "**[P2]** stale old commit finding",
                                                "url": "https://example.invalid/stale",
                                                "commit": {"oid": old_sha},
                                                "originalCommit": {"oid": old_sha},
                                            }
                                        ]
                                    },
                                },
                                {
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "openai-codex"},
                                                "body": "**[P1]** unresolved stale thread without commit metadata",
                                                "url": "https://example.invalid/no-commit-metadata",
                                                "commit": None,
                                                "originalCommit": None,
                                            }
                                        ]
                                    },
                                },
                            ],
                        }
                    }
                }
            }
        }
    ]

    monkeypatch.setattr(module, "graphql", lambda *_args, **_kwargs: pages[0])

    urls = module.unresolved_codex_finding_thread_urls(
        "Soju06/codex-lb",
        714,
        head_sha=head_sha,
        allowed_authors={"openai-codex"},
    )

    assert urls == (
        "https://example.invalid/reanchored-current",
        "https://example.invalid/current",
        "https://example.invalid/fallback",
    )


def test_resolved_inline_codex_finding_does_not_count_as_review_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_sync_module()

    monkeypatch.setattr(
        module,
        "paged_api",
        lambda _path: [
            {
                "body": "**P1 Badge** resolved finding",
                "commit_id": "a" * 40,
                "original_commit_id": "a" * 40,
                "pull_request_review_id": 123,
                "html_url": "https://github.test/review/resolved",
                "created_at": "2026-06-14T00:00:00Z",
                "user": {"login": "chatgpt-codex-connector"},
            }
        ],
    )
    monkeypatch.setattr(module, "unresolved_review_comment_urls", lambda *_args: set())

    assert module.pull_review_comment_nodes("Soju06/codex-lb", 714, head_sha="a" * 40) == []


def test_unresolved_inline_codex_finding_counts_as_review_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_sync_module()
    url = "https://github.test/review/unresolved"

    monkeypatch.setattr(
        module,
        "paged_api",
        lambda _path: [
            {
                "body": "**P1 Badge** unresolved finding",
                "commit_id": "a" * 40,
                "original_commit_id": "a" * 40,
                "pull_request_review_id": 123,
                "html_url": url,
                "created_at": "2026-06-14T00:00:00Z",
                "user": {"login": "chatgpt-codex-connector"},
            }
        ],
    )
    monkeypatch.setattr(module, "unresolved_review_comment_urls", lambda *_args: {url})

    nodes = module.pull_review_comment_nodes("Soju06/codex-lb", 714, head_sha="a" * 40)

    assert len(nodes) == 1
    assert nodes[0]["url"] == url


def _rate_limited_proc() -> Any:
    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "gh: API rate limit exceeded for user ID 34199905 (HTTP 403)"

    return _Proc()


def _ok_proc(payload: str = "{}") -> Any:
    class _Proc:
        returncode = 0
        stdout = payload
        stderr = ""

    return _Proc()


def _transient_gh_proc() -> Any:
    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "gh: HTTP 503"

    return _Proc()


def test_run_gh_switches_to_fallback_token_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    monkeypatch.setenv("GH_TOKEN", "primary-token")
    monkeypatch.setenv("GH_FALLBACK_TOKEN", "fallback-token")

    calls: list[str] = []

    def fake_run(command: Any, **kwargs: Any) -> Any:
        import os

        calls.append(os.environ["GH_TOKEN"])
        if len(calls) == 1:
            return _rate_limited_proc()
        return _ok_proc()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.run_gh(["api", "/rate-limited-path"])

    assert result == {}
    assert calls == ["primary-token", "fallback-token"]


def test_run_gh_retries_transient_read_only_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        del kwargs
        calls.append(command)
        if len(calls) == 1:
            return _transient_gh_proc()
        return _ok_proc('{"ok": true}')

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    assert module.run_gh(["api", "/repos/example/project/issues/1/labels"]) == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_run_gh_does_not_retry_mutating_pr_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        del kwargs
        calls.append(command)
        return _transient_gh_proc()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    with pytest.raises(module.GhError):
        module.run_gh(["pr", "comment", "1344", "--body", "@codex review"])
    assert len(calls) == 1


def test_run_gh_activates_fallback_without_retrying_identity_sensitive_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_sync_module()
    monkeypatch.setenv("GH_TOKEN", "primary-token")
    monkeypatch.setenv("GH_FALLBACK_TOKEN", "fallback-token")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        del kwargs
        calls.append(command)
        return _rate_limited_proc()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.GhError, match="without retrying this identity-sensitive command"):
        module.run_gh(
            ["api", "--method", "POST", "/repos/example/project/issues/1/comments"],
            fallback_retry=False,
        )

    assert len(calls) == 1
    # The fallback still activates so the rest of the run stays alive.
    assert module._fallback_token_active is True
    import os

    assert os.environ["GH_TOKEN"] == "fallback-token"


def test_trigger_codex_review_posts_without_fallback_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    captured_kwargs: list[dict[str, Any]] = []

    def capture_run_gh(_args: list[str], **kwargs: Any) -> None:
        captured_kwargs.append(kwargs)

    monkeypatch.setattr(module, "run_gh", capture_run_gh)

    warnings = module.trigger_codex_review(
        decision(module, trigger_codex_review=True, ok_action="keep"),
        body="@codex review",
    )

    assert warnings == ()
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["fallback_retry"] is False


def test_run_gh_fails_without_distinct_fallback_token(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    monkeypatch.setenv("GH_TOKEN", "primary-token")
    monkeypatch.setenv("GH_FALLBACK_TOKEN", "primary-token")

    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: _rate_limited_proc())

    with pytest.raises(module.GhError):
        module.run_gh(["api", "/rate-limited-path"])


def test_run_gh_fails_when_fallback_token_is_also_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_sync_module()
    monkeypatch.setenv("GH_TOKEN", "primary-token")
    monkeypatch.setenv("GH_FALLBACK_TOKEN", "fallback-token")

    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: _rate_limited_proc())

    with pytest.raises(module.GhError):
        module.run_gh(["api", "/rate-limited-path"])

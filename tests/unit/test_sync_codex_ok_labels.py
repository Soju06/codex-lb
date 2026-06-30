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

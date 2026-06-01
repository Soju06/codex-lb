from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_cleanup_plan_closes_only_superseded_managed_beta_prs() -> None:
    from scripts.cleanup_superseded_beta_prs import cleanup_plan

    prs = [
        {
            "number": 807,
            "title": "chore: release v1.19.1-beta.1",
            "headRefName": "release/beta-1.19.1-beta.1",
            "headRepositoryOwner": {"login": "Soju06"},
            "labels": [],
            "body": "Synced automatically from release-please PR #806; no manual beta workflow dispatch is required.",
        },
        {
            "number": 827,
            "title": "chore: release v1.20.0-beta.1",
            "headRefName": "release/beta-1.20.0-beta.1",
            "headRepositoryOwner": {"login": "Soju06"},
            "labels": [],
            "body": "Synced automatically from release-please PR #806; no manual beta workflow dispatch is required.",
        },
        {
            "number": 900,
            "title": "chore: release v1.18.0-beta.1",
            "headRefName": "release/beta-1.18.0-beta.1",
            "headRepositoryOwner": {"login": "Soju06"},
            "labels": [{"name": "pinned"}],
            "body": "Synced automatically from release-please PR #806; no manual beta workflow dispatch is required.",
        },
        {
            "number": 901,
            "title": "manual beta experiment",
            "headRefName": "release/beta-manual-test",
            "headRepositoryOwner": {"login": "Soju06"},
            "labels": [],
            "body": "Manual release testing branch.",
        },
        {
            "number": 902,
            "title": "chore: release v1.17.0-beta.1",
            "headRefName": "release/beta-1.17.0-beta.1",
            "headRepositoryOwner": {"login": "external"},
            "labels": [],
            "body": "Synced automatically from release-please PR #806; no manual beta workflow dispatch is required.",
        },
    ]

    plan = cleanup_plan(
        prs,
        current_branch="release/beta-1.20.0-beta.1",
        current_tag="v1.20.0-beta.1",
        repo="Soju06/codex-lb",
        repo_owner="Soju06",
        release_pr="806",
    )

    assert [action.pr_number for action in plan] == [807]
    assert plan[0].head_ref == "release/beta-1.19.1-beta.1"
    assert plan[0].delete_ref_api_path == "repos/Soju06/codex-lb/git/refs/heads/release/beta-1.19.1-beta.1"
    assert "v1.20.0-beta.1" in plan[0].comment
    assert "release-please PR #806" in plan[0].comment


def test_execute_cleanup_comments_closes_and_deletes_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import cleanup_superseded_beta_prs as cleanup

    calls: list[list[str]] = []

    def fake_run_gh(args: list[str]) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(cleanup, "_run_gh", fake_run_gh)

    cleanup.execute_cleanup(
        [
            cleanup.CleanupAction(
                pr_number=807,
                head_ref="release/beta-1.19.1-beta.1",
                comment="Superseded by v1.20.0-beta.1",
                delete_ref_api_path="repos/Soju06/codex-lb/git/refs/heads/release/beta-1.19.1-beta.1",
            )
        ],
        repo="Soju06/codex-lb",
        dry_run=False,
    )

    assert calls == [
        [
            "pr",
            "comment",
            "807",
            "--repo",
            "Soju06/codex-lb",
            "--body",
            "Superseded by v1.20.0-beta.1",
        ],
        ["pr", "close", "807", "--repo", "Soju06/codex-lb"],
        [
            "api",
            "-X",
            "DELETE",
            "repos/Soju06/codex-lb/git/refs/heads/release/beta-1.19.1-beta.1",
        ],
    ]


def test_cleanup_script_dry_run_lists_superseded_pr(tmp_path: Path) -> None:
    prs = [
        {
            "number": 807,
            "title": "chore: release v1.19.1-beta.1",
            "headRefName": "release/beta-1.19.1-beta.1",
            "headRepositoryOwner": {"login": "Soju06"},
            "labels": [],
            "body": "Synced automatically from release-please PR #806; no manual beta workflow dispatch is required.",
        }
    ]
    gh = tmp_path / "gh"
    calls = tmp_path / "calls.jsonl"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(calls)!r}).write_text(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"print({json.dumps(prs)!r})\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.cleanup_superseded_beta_prs",
            "--repo",
            "Soju06/codex-lb",
            "--repo-owner",
            "Soju06",
            "--current-branch",
            "release/beta-1.20.0-beta.1",
            "--current-tag",
            "v1.20.0-beta.1",
            "--release-pr",
            "806",
            "--dry-run",
        ],
        cwd=Path(__file__).parents[2],
        env={"PATH": f"{tmp_path}:{Path('/usr/bin')}", "PYTHONPATH": str(Path(__file__).parents[2])},
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    assert "Would close PR #807" in result.stdout
    assert "release/beta-1.19.1-beta.1" in result.stdout
    assert json.loads(calls.read_text(encoding="utf-8"))[0:4] == [
        "pr",
        "list",
        "--repo",
        "Soju06/codex-lb",
    ]

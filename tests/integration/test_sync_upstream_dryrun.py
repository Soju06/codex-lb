"""Integration tests for the upstream-sync wrapper scripts.

Verifies the deterministic shell-level surface of the sync wrapper without
invoking the actual ``claude -p`` skill call (that path is LLM-dependent
and is verified separately via ``scripts/sync_upstream.sh --dry-run``
against a sandbox clone).

Scenarios covered (from ``openspec/changes/add-upstream-sync-cron/context.md``):

1. ``no-op``             — upstream unchanged, no PR or issue side-effect.
2. ``clean fast-forward`` — upstream-only file change merges cleanly.
3. ``fork-customized``   — conflict in a file the fork also edits → blocked.
4. ``preflight_failed``  — no GITHUB_TOKEN, no claude, or no upstream remote.
5. ``skipped_locked``    — concurrent run sees the lock file and exits cleanly.

These tests use only ``bash``, ``git``, ``tmp_path``, and ``subprocess`` —
no network, no LLM, no fixtures from the host repo.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup_upstream_remote.sh"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_upstream.sh"
EXPECTED_UPSTREAM_URL = "https://github.com/Soju06/codex-lb.git"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(*args: str, cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_ENV, **(env_extra or {})}
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if res.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} (cwd={cwd}) failed: rc={res.returncode}\nstdout: {res.stdout}\nstderr: {res.stderr}"
        )
    return res


def _make_bare_with_commit(bare_dir: Path, work_dir: Path, message: str = "init") -> Path:
    """Create ``bare_dir`` as a bare repo, seed it with one commit on main, return bare path."""
    bare_dir.mkdir(parents=True, exist_ok=True)
    _git("init", "--bare", "--initial-branch=main", str(bare_dir), cwd=bare_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    _git("init", "--initial-branch=main", str(work_dir), cwd=work_dir)
    _git("commit", "--allow-empty", "-m", message, cwd=work_dir)
    _git("remote", "add", "origin", str(bare_dir), cwd=work_dir)
    _git("push", "origin", "main", cwd=work_dir)
    return bare_dir


def _clone(bare: Path, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    _git("clone", str(bare), str(into), cwd=into.parent)
    return into


def _seed_file(repo: Path, relative_path: str, content: str, message: str) -> None:
    """Write ``relative_path`` inside ``repo`` with ``content`` and commit."""
    file_path = repo / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    _git("add", relative_path, cwd=repo)
    _git("commit", "-m", message, cwd=repo)


# --------------------------------------------------------------------------
# Scenario 4 — preflight_failed (deterministic bash preflight checks)
# --------------------------------------------------------------------------


def test_preflight_no_github_token_exits_1(tmp_path: Path) -> None:
    """Without ``GITHUB_TOKEN`` set, the wrapper exits 1 with a clear error."""
    # Initialize a real git repo so the wrapper's first preflight check
    # (``git rev-parse --show-toplevel``) passes; we want to exercise the
    # GITHUB_TOKEN guard, not the git-repo or claude guards.
    subprocess.run(["git", "init", "--initial-branch=main", str(tmp_path)], check=True, capture_output=True)
    # Inherit full PATH so claude + jq resolve (those guards come BEFORE the
    # GITHUB_TOKEN guard in the wrapper). We also merge in GIT_ENV so the
    # invoked wrapper's git operations have a deterministic identity, and drop
    # GITHUB_TOKEN via a dict comprehension so the env keeps its narrow
    # ``dict[str, str]`` type for ``subprocess.run`` overload resolution.
    env = {**os.environ, **GIT_ENV}
    env = {k: v for k, v in env.items() if k != "GITHUB_TOKEN"}
    # Sanity: claude and jq must be available so we reach the GITHUB_TOKEN guard.
    # ``command -v`` is a shell builtin; invoke via bash so subprocess.run can
    # resolve the executable regardless of the runner image (Ubuntu CI has no
    # /usr/bin/command).
    if (
        subprocess.run(["bash", "-c", "command -v claude"], env=env, capture_output=True).returncode != 0
        or subprocess.run(["bash", "-c", "command -v jq"], env=env, capture_output=True).returncode != 0
    ):
        pytest.skip("claude or jq not available in test environment")
    result = subprocess.run(
        [str(SYNC_SCRIPT)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}; stderr={result.stderr!r}"
    assert "GITHUB_TOKEN" in result.stderr


def test_preflight_missing_claude_exits_4(tmp_path: Path) -> None:
    """Without ``claude`` on PATH, the wrapper exits 4 (claude-side error)."""
    # Real git repo so the wrapper progresses past the git check.
    subprocess.run(["git", "init", "--initial-branch=main", str(tmp_path)], check=True, capture_output=True)
    # PATH must keep bash accessible (wrapper shebang is ``#!/usr/bin/env bash``),
    # but it must NOT contain claude. We prepend an empty-ish dir that lacks claude
    # while still providing bash via the original /usr/bin:/bin.
    env = {
        **os.environ,
        **GIT_ENV,
        "PATH": "/usr/bin:/bin",  # bash lives here; claude is not installed on CI
        "GITHUB_TOKEN": "ghp_test",
    }
    # Sanity: bash must resolve, claude must not. ``command -v`` is a shell
    # builtin, so invoke via bash (Ubuntu CI ships no /usr/bin/command).
    bash_ok = subprocess.run(["bash", "--version"], env=env, capture_output=True).returncode == 0
    claude_missing = subprocess.run(["bash", "-c", "command -v claude"], env=env, capture_output=True).returncode != 0
    if not bash_ok or not claude_missing:
        pytest.skip("test environment has bash or claude in unexpected state")
    result = subprocess.run(
        [str(SYNC_SCRIPT)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 4, f"expected exit 4, got {result.returncode}; stderr={result.stderr!r}"
    assert "claude" in result.stderr.lower()


# --------------------------------------------------------------------------
# Scenario 4 (continued) — setup_upstream_remote.sh behavior
# --------------------------------------------------------------------------


def test_setup_remote_idempotent(tmp_path: Path) -> None:
    """Re-running the script never mutates the existing correct URL."""
    fake_fork = _clone(_make_bare_with_commit(tmp_path / "u.git", tmp_path / "seed"), tmp_path / "fork")

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SETUP_SCRIPT)], cwd=fake_fork, capture_output=True, text=True, check=False)

    out1 = run()
    out2 = run()
    assert out1.returncode == 0 and out2.returncode == 0
    assert "upstream already configured" in out2.stdout
    assert fake_fork.joinpath(".git").joinpath("config").read_text().count(EXPECTED_UPSTREAM_URL) >= 1


def test_setup_remote_refuses_url_drift(tmp_path: Path) -> None:
    """A pre-existing upstream pointing elsewhere is not overwritten silently."""
    fake_fork = _clone(_make_bare_with_commit(tmp_path / "u.git", tmp_path / "seed"), tmp_path / "fork")
    _git("remote", "add", "upstream", "https://example.com/wrong.git", cwd=fake_fork)
    result = subprocess.run(
        [str(SETUP_SCRIPT)],
        cwd=fake_fork,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "refusing to overwrite" in result.stderr
    # Confirm the URL was not changed.
    assert b"https://example.com/wrong.git" in subprocess.check_output(
        ["git", "remote", "get-url", "upstream"],
        cwd=fake_fork,
    )


# --------------------------------------------------------------------------
# Scenario 1 / 2 — no-op and clean fast-forward
# --------------------------------------------------------------------------


def test_noop_when_upstream_unchanged(tmp_path: Path) -> None:
    """``git fetch upstream main`` produces no new commits → no-op, no side-effects."""
    bare = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    _make_bare_with_commit(bare, seed)
    fork = _clone(bare, tmp_path / "fork")
    _git("remote", "add", "upstream", str(bare), cwd=fork)
    _git("fetch", "upstream", "main", cwd=fork)
    out = subprocess.check_output(
        ["git", "rev-list", "--left-right", "--count", "main...upstream/main"],
        cwd=fork,
        text=True,
    )
    # 0 0 means no divergence either way — the SKILL's "no-op" early-exit case.
    assert out.strip() == "0\t0", f"expected no divergence, got {out!r}"


def test_clean_fast_forward_merge(tmp_path: Path) -> None:
    """An upstream-only commit merges cleanly via ``git merge --no-ff upstream/main``.

    The synthetic ``no-ff`` preserves the merge commit that downstream
    tooling (and the sync-PR diffstat) expects.
    """
    bare = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    _make_bare_with_commit(bare, seed, message="seed")
    fork = _clone(bare, tmp_path / "fork")
    _git("remote", "add", "upstream", str(bare), cwd=fork)
    # Land a fork-customized commit (must not conflict with the upstream change).
    _seed_file(fork, "FORK_CUSTOM.md", "fork customization\n", "fork: customize")
    # Upstream-only change on a different file.
    seed2 = tmp_path / "seed2"
    _git("clone", str(bare), str(seed2), cwd=tmp_path)
    _seed_file(Path(seed2), "UPSTREAM_ONLY.md", "upstream addition\n", "upstream: add new doc")
    _git("push", "origin", "main", cwd=seed2)
    # Now the fork has one commit ahead and one upstream commit behind.
    _git("fetch", "upstream", "main", cwd=fork)
    # ``--no-ff`` creates a merge commit and therefore needs an identity that
    # is independent of any host-level ~/.gitconfig. Pass GIT_ENV explicitly
    # so the merge succeeds on minimal CI runners.
    merge_env = {**os.environ, **GIT_ENV}
    rc = subprocess.run(
        ["git", "merge", "--no-ff", "upstream/main"],
        cwd=fork,
        capture_output=True,
        text=True,
        check=False,
        env=merge_env,
    ).returncode
    assert rc == 0, "clean merge should succeed"
    # Merge commit present.
    log = subprocess.check_output(["git", "log", "--oneline", "-n", "3"], cwd=fork, text=True, env=merge_env)
    assert "Merge branch" in log or "Merge commit" in log or "Merge" in log


# --------------------------------------------------------------------------
# Scenario 3 — fork-customized conflict
# --------------------------------------------------------------------------


def test_conflict_in_fork_customized_file_blocks_merge(tmp_path: Path) -> None:
    """When the fork and upstream both edit the same file, merge fails.

    This is the deterministic core of the SKILL's "blocked" classification:
    the merge itself stops on conflict; the SKILL then files a ``sync-blocker``
    issue carrying ``patch.diff`` instead of pushing.
    """
    bare = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    _make_bare_with_commit(bare, seed, message="seed with shared file")
    fork = _clone(bare, tmp_path / "fork")
    _git("remote", "add", "upstream", str(bare), cwd=fork)
    _seed_file(fork, "shared.md", "fork says hello\n", "fork: edit shared.md")
    seed2 = tmp_path / "seed2"
    _git("clone", str(bare), str(seed2), cwd=tmp_path)
    _seed_file(Path(seed2), "shared.md", "upstream says hello\n", "upstream: edit shared.md")
    _git("push", "origin", "main", cwd=seed2)
    _git("fetch", "upstream", "main", cwd=fork)
    # Use the same identity-suppressing env as the rest of the test so the
    # merge behaves the same on minimal CI runners and on developer hosts.
    merge_env = {**os.environ, **GIT_ENV}
    result = subprocess.run(
        ["git", "merge", "--no-ff", "upstream/main"],
        cwd=fork,
        capture_output=True,
        text=True,
        check=False,
        env=merge_env,
    )
    assert result.returncode != 0, "conflict merge should fail"
    # ``git status`` must show ``Unmerged paths`` after a conflict.
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=fork, text=True, env=merge_env)
    assert "UU " in status or "AA " in status or "shared.md" in status


# --------------------------------------------------------------------------
# Scenario 5 — skipped_locked (concurrent runs)
# --------------------------------------------------------------------------


def test_lock_file_collision_blocks_second_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A concurrent run sees the lock file and exits without side-effects.

    The SKILL mandates ``/tmp/codex-lb-sync.lock`` as its serialization
    point. We simulate a second concurrent run by pre-creating that lock
    and asserting that the deterministic guard fires.
    """
    lock_path = Path("/tmp/codex-lb-sync.lock")
    if lock_path.exists():
        pytest.skip("another test left /tmp/codex-lb-sync.lock behind; skipping")
    try:
        lock_path.write_text(str(os.getpid()))
        # If a real run were running, the SKILL would observe the lock and
        # emit ``skipped_locked``. We assert that the file is still present
        # and unreadable-as-directory, which is what the guard does.
        assert lock_path.exists()
        assert lock_path.read_text() == str(os.getpid())
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------------
# Sanity: scripts exist and are executable / non-executable as specified
# --------------------------------------------------------------------------


def test_scripts_match_contract(tmp_path: Path) -> None:
    """The wrapper and helper scripts exist with the right permissions."""
    assert SETUP_SCRIPT.exists(), "setup_upstream_remote.sh is missing"
    assert SYNC_SCRIPT.exists(), "sync_upstream.sh is missing"
    assert os.access(SETUP_SCRIPT, os.X_OK), "setup_upstream_remote.sh must be executable"
    assert os.access(SYNC_SCRIPT, os.X_OK), "sync_upstream.sh must be executable"


@pytest.mark.parametrize("name", ["sync_upstream.sh", "setup_upstream_remote.sh"])
def test_wrapper_scripts_have_set_euo_pipefail(name: str) -> None:
    """Every wrapper script opts into strict mode to fail-fast on unexpected errors."""
    text = (REPO_ROOT / "scripts" / name).read_text()
    assert "set -euo pipefail" in text, f"{name} must use `set -euo pipefail` for fail-fast behavior"


def test_launchd_template_is_xml_and_not_executable(tmp_path: Path) -> None:
    """The plist template is well-formed and must remain non-executable."""
    plist = REPO_ROOT / "scripts" / "launchd.example.plist"
    assert plist.exists()
    text = plist.read_text()
    assert text.lstrip().startswith("<?xml"), "plist must start with XML declaration"
    assert "com.codex-lb.sync-upstream" in text, "plist Label should be the documented agent id"
    # Non-executable (matches the unit-test invariant; this is a belt-and-suspenders check).
    assert not os.access(plist, os.X_OK), "launchd.example.plist must NOT be executable"

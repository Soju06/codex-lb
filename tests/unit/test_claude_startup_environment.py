from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_claude_lb_launch import load_launcher_module


@pytest.mark.parametrize(
    ("soft", "hard", "expected"), [(256, 4096, 4096), (8192, 131072, 65536), (70000, 131072, None)]
)
def test_startup_limit_respects_existing_limits(monkeypatch, soft, hard, expected):
    launcher = load_launcher_module()
    calls = []
    monkeypatch.setattr(launcher.resource, "getrlimit", lambda _: (soft, hard))
    monkeypatch.setattr(launcher.resource, "setrlimit", lambda which, value: calls.append(value))
    launcher._raise_fd_soft_limit(65536)
    assert calls == ([] if expected is None else [(expected, hard)])


def test_limit_denial_does_not_break_startup(monkeypatch):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher.resource, "getrlimit", lambda _: (256, 4096))

    def denied(*args):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(launcher.resource, "setrlimit", denied)
    launcher._raise_fd_soft_limit(65536)


@pytest.mark.parametrize("code", [errno.EPERM, errno.EACCES, errno.ENOENT])
def test_inaccessible_cwd_fails_before_proxy_or_claude(monkeypatch, capsys, code):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher.sys, "argv", ["claude-lb-launch"])

    def unreadable(*args):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(launcher.os, "scandir", unreadable)
    monkeypatch.setattr(launcher, "schedule_opus_doctor", lambda: pytest.fail("doctor must not start"))
    monkeypatch.setattr(launcher, "start_lb_proxy", lambda *_: pytest.fail("proxy must not start"))
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 1
    assert "cannot read the current working directory" in capsys.readouterr().err


def test_execed_claude_inherits_raised_soft_limit(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "clients/claude-lb-launch"
    claude = tmp_path / "claude"
    claude.write_text(f"#!{sys.executable}\nimport resource\nprint(resource.getrlimit(resource.RLIMIT_NOFILE)[0])\n")
    claude.chmod(0o755)
    driver = (
        "import os,resource,sys; "
        "resource.setrlimit(resource.RLIMIT_NOFILE,(256,4096)); "
        "os.execv(sys.executable,[sys.executable,sys.argv[1],'--version'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", driver, str(launcher)],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "CLAUDE_LB_DISABLE": "1", "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "4096"

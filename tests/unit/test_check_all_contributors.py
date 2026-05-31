from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker_module():
    script_path = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_all_contributors.py"
    spec = importlib.util.spec_from_file_location("check_all_contributors", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_noreply_regex_accepts_current_and_legacy_github_formats():
    checker = _load_checker_module()

    current = checker.NOREPLY_RE.match("12345+octocat@users.noreply.github.com")
    legacy = checker.NOREPLY_RE.match("SHAREN@users.noreply.github.com")

    assert current is not None
    assert current.group(1) == "octocat"
    assert legacy is not None
    assert legacy.group(1) == "SHAREN"

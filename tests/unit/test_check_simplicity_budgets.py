from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_checker_module():
    script_path = REPO_ROOT / ".github" / "scripts" / "check_simplicity_budgets.py"
    spec = importlib.util.spec_from_file_location("check_simplicity_budgets", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NAV_SOURCE = """
const NAV_ITEMS = [
  { to: "/dashboard", labelKey: "nav.dashboard" },
  { to: "/reports", labelKey: "nav.reports" },
  { to: "/settings", labelKey: "nav.settings" },
] as const;
"""


def _write_repo(
    tmp_path: Path,
    *,
    readme: str = "# Title\n\nbody\n",
    env_example: str = "# A=1\n",
    nav_source: str = NAV_SOURCE,
    max_lines: int = 200,
    max_headings: int = 10,
    max_env_lines: int = 60,
    max_nav_items: int = 6,
) -> None:
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / ".env.example").write_text(env_example, encoding="utf-8")
    (tmp_path / "nav.tsx").write_text(nav_source, encoding="utf-8")
    config_dir = tmp_path / ".github"
    config_dir.mkdir()
    (config_dir / "simplicity-budgets.toml").write_text(
        f"""
[readme]
path = "README.md"
max_lines = {max_lines}
max_top_level_headings = {max_headings}

[env_example]
path = ".env.example"
max_lines = {max_env_lines}

[core_nav]
path = "nav.tsx"
array = "NAV_ITEMS"
max_items = {max_nav_items}
""",
        encoding="utf-8",
    )


def test_heading_count_ignores_fenced_code_blocks():
    checker = _load_checker_module()

    lines = [
        "# Title",
        "## Real section",
        "```bash",
        "# comment inside a fence, not a heading",
        "## also not a heading",
        "```",
        "## Another real section",
        "### h3 is not top-level",
    ]

    assert checker.count_top_level_headings(lines) == 3


def test_heading_count_handles_fence_reopen():
    checker = _load_checker_module()

    lines = [
        "```python",
        "# fenced",
        "```",
        "# heading between fences",
        "```",
        "# fenced again",
        "```",
    ]

    assert checker.count_top_level_headings(lines) == 1


def test_heading_count_handles_tilde_and_indented_fences():
    checker = _load_checker_module()

    lines = [
        "# Title",
        "~~~text",
        "# inside a tilde fence",
        "~~~",
        "   ```bash",
        "# inside an indented backtick fence",
        "   ```",
        "## Real section",
    ]

    assert checker.count_top_level_headings(lines) == 2


def test_heading_count_fence_close_must_match_opening_character():
    checker = _load_checker_module()

    lines = [
        "```",
        "~~~",
        "# still inside the backtick fence",
        "```",
        "# heading after the fence",
    ]

    assert checker.count_top_level_headings(lines) == 1


def test_heading_count_close_fence_must_have_no_info_string():
    checker = _load_checker_module()

    lines = [
        "```",
        "```bash",
        "# still inside: a close fence may not carry an info string",
        "```",
        "# heading after the real close",
    ]

    assert checker.count_top_level_headings(lines) == 1


def test_strip_contributors_block_removes_markers_and_body():
    checker = _load_checker_module()

    lines = [
        "# Title",
        "<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->",
        "<table>generated</table>",
        "## Generated heading that must not count",
        "<!-- ALL-CONTRIBUTORS-LIST:END -->",
        "tail",
    ]

    stripped = checker.strip_contributors_block(lines)

    assert stripped == ["# Title", "tail"]


def test_strip_contributors_block_unclosed_exits_2(capsys):
    checker = _load_checker_module()

    lines = [
        "# Title",
        "<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->",
        "<table>generated</table>",
        "tail that would be silently swallowed",
    ]

    with pytest.raises(SystemExit) as excinfo:
        checker.strip_contributors_block(lines)

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "ALL-CONTRIBUTORS-LIST:END" in out


def test_main_missing_config_exits_2(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        checker.main()

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "not found" in out


def test_main_malformed_config_exits_2(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    config_dir = tmp_path / ".github"
    config_dir.mkdir()
    (config_dir / "simplicity-budgets.toml").write_text("[readme\nnot toml", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        checker.main()

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "not valid TOML" in out


def test_main_passes_within_budgets(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    _write_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    assert checker.main() == 0

    out = capsys.readouterr().out
    assert "OK" in out
    assert "::error" not in out


def test_main_counts_readme_lines_after_stripping_contributors_block(tmp_path, monkeypatch):
    checker = _load_checker_module()
    generated = (
        ["<!-- ALL-CONTRIBUTORS-LIST:START -->"] + ["<td>row</td>"] * 50 + ["<!-- ALL-CONTRIBUTORS-LIST:END -->"]
    )
    readme = "\n".join(["# Title", "intro", *generated]) + "\n"
    _write_repo(tmp_path, readme=readme, max_lines=5)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    assert checker.main() == 0


def test_main_fails_when_readme_over_line_budget(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    _write_repo(tmp_path, readme="# Title\n" + "line\n" * 50, max_lines=10)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    assert checker.main() == 1

    out = capsys.readouterr().out
    assert "::error file=README.md::" in out
    assert "simplicity-budget-approved" in out
    assert "re-run" in out.lower()
    assert "merge_group" in out


def test_main_fails_when_env_example_over_budget(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    _write_repo(tmp_path, env_example="# A=1\n" * 61)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    assert checker.main() == 1

    assert "::error file=.env.example::" in capsys.readouterr().out


def test_main_fails_when_nav_over_budget(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    _write_repo(tmp_path, max_nav_items=2)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    assert checker.main() == 1

    assert "core nav items (NAV_ITEMS): 3/2 OVER" in capsys.readouterr().out


def test_override_label_downgrades_violations_to_warnings(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    _write_repo(tmp_path, readme="# Title\n" + "line\n" * 50, max_lines=10)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_LABELS", '["db migration", "simplicity-budget-approved"]')

    assert checker.main() == 0

    out = capsys.readouterr().out
    assert "::warning file=README.md::" in out
    assert "::error" not in out
    assert "passing with warnings" in out


def test_override_label_ignored_on_null_payload(tmp_path, monkeypatch):
    checker = _load_checker_module()
    _write_repo(tmp_path, readme="# Title\n" + "line\n" * 50, max_lines=10)
    monkeypatch.chdir(tmp_path)
    # push / merge_group events render the PR_LABELS expression to "null"
    monkeypatch.setenv("PR_LABELS", "null")

    assert checker.main() == 1


def test_heading_count_respects_fence_length():
    checker = _load_checker_module()

    lines = [
        "````markdown",
        "```",
        "# still inside the four-backtick fence",
        "```",
        "````",
        "# heading after the outer fence",
    ]

    assert checker.count_top_level_headings(lines) == 1


def test_nav_count_ignores_nested_arrays_inside_items(tmp_path):
    checker = _load_checker_module()
    nav_file = tmp_path / "nav.tsx"
    nav_file.write_text(
        "const NAV_ITEMS = [\n"
        '  { to: "/dashboard", roles: ["admin"] },\n'
        '  { to: "/reports", labelKey: "nav.reports" },\n'
        '  { to: "/settings", labelKey: "nav.settings" },\n'
        "] as const;\n",
        encoding="utf-8",
    )

    assert checker.count_nav_items(nav_file, "NAV_ITEMS") == 3


def test_nav_count_stops_at_target_array_when_file_has_multiple(tmp_path):
    checker = _load_checker_module()
    nav_file = tmp_path / "nav.tsx"
    nav_file.write_text(
        "const CORE_NAV_ITEMS = [\n"
        '  { to: "/dashboard" },\n'
        '  { to: "/settings" },\n'
        "] as const;\n"
        "\n"
        "const ADVANCED_NAV_ITEMS = [\n"
        '  { to: "/automations" },\n'
        "] as const;\n",
        encoding="utf-8",
    )

    assert checker.count_nav_items(nav_file, "CORE_NAV_ITEMS") == 2
    assert checker.count_nav_items(nav_file, "ADVANCED_NAV_ITEMS") == 1


def test_heading_count_includes_indented_headings():
    checker = _load_checker_module()

    lines = [
        "# Title",
        "  ## indented but still a heading (up to 3 spaces)",
        "    # 4+ spaces is an indented code block, not a heading",
    ]

    assert checker.count_top_level_headings(lines) == 2


def test_nav_count_survives_nested_as_const_inside_items(tmp_path):
    checker = _load_checker_module()
    nav_file = tmp_path / "nav.tsx"
    nav_file.write_text(
        "const NAV_ITEMS = [\n"
        '  { to: "/dashboard", matches: ["/foo"] as const },\n'
        '  { to: "/reports" },\n'
        '  { to: "/settings" },\n'
        "] as const;\n",
        encoding="utf-8",
    )

    assert checker.count_nav_items(nav_file, "NAV_ITEMS") == 3


def test_nav_count_rejects_suffixed_identifier(tmp_path, capsys):
    checker = _load_checker_module()
    nav_file = tmp_path / "nav.tsx"
    nav_file.write_text(
        'const NAV_ITEMS_V2 = [\n  { to: "/dashboard" },\n  { to: "/reports" },\n] as const;\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as excinfo:
        checker.count_nav_items(nav_file, "NAV_ITEMS")

    assert excinfo.value.code == 2
    assert "NAV_ITEMS" in capsys.readouterr().out


def test_main_config_missing_key_exits_2(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    _write_repo(tmp_path)
    config_path = tmp_path / ".github" / "simplicity-budgets.toml"
    config = config_path.read_text(encoding="utf-8").replace("max_top_level_headings", "typo_key")
    config_path.write_text(config, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        checker.main()

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "section/key" in out


def test_missing_nav_array_exits_2_with_repoint_instruction(tmp_path, monkeypatch, capsys):
    checker = _load_checker_module()
    _write_repo(tmp_path, nav_source="const OTHER_ITEMS = [] as const;\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        checker.main()

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "NAV_ITEMS" in out
    assert "simplicity-budgets.toml" in out


def test_missing_nav_file_exits_2(tmp_path, monkeypatch):
    checker = _load_checker_module()
    _write_repo(tmp_path)
    (tmp_path / "nav.tsx").unlink()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_LABELS", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        checker.main()

    assert excinfo.value.code == 2


def test_nav_count_in_live_tree_is_within_configured_budget():
    """The live tree satisfies its own [core_nav] budget, wherever the TOML points.

    Config-driven on purpose: a nav refactor repoints .github/simplicity-budgets.toml
    (path/array/max_items) as its ONLY required edit, and this test follows it.
    """
    checker = _load_checker_module()

    config = tomllib.loads((REPO_ROOT / ".github" / "simplicity-budgets.toml").read_text(encoding="utf-8"))
    nav_cfg = config["core_nav"]

    count = checker.count_nav_items(REPO_ROOT / nav_cfg["path"], nav_cfg["array"])

    assert count > 0
    assert count <= nav_cfg["max_items"]

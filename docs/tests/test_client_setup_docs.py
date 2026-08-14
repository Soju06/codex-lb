from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_client_setup_config_only_wrapper_keeps_retag_explicit() -> None:
    client_setup = (REPO_ROOT / "docs" / "client-setup.md").read_text(encoding="utf-8")
    auto_section = client_setup.split("### Automatic Shell Integration", 1)[1].split("## OpenCode", 1)[0]

    assert "config-only" in auto_section
    assert "codex_lb_uvx" in auto_section
    assert 'command uvx codex-lb "$@"' in auto_section
    assert "return $status" in auto_section
    assert "codex-lb codex-sessions retag --from openai --to codex-lb --yes" in auto_section
    assert "instead of overriding `uvx`" in auto_section
    assert "state_*.sqlite" in auto_section
    assert "sed -i ''" not in auto_section
    assert 'find "$HOME/.codex/sessions"' not in auto_section
    assert 'sqlite3 "$db_file"' not in auto_section
    assert "trap '_do_cleanup' EXIT INT TERM" not in auto_section

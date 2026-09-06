from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

from app.modules.key_dashboard.install import InstallPlatform, build_install_script

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("platform", ["macos", "linux"])
@pytest.mark.parametrize("model", [None, "gpt-5.6-sol", 'odd "model"\n$(touch injected)\n\'@\nCODEX_LB_CONFIG\n😀'])
def test_installer_writes_safe_private_files_and_recoverable_backups(
    tmp_path: Path, platform: InstallPlatform, model: str | None
) -> None:
    codex_dir = tmp_path / "codex home"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text('model = "old-model"\n', encoding="utf-8")
    (codex_dir / "auth.json").write_text('{"old": "auth"}', encoding="utf-8")
    (codex_dir / "unrelated.txt").write_text("unchanged", encoding="utf-8")
    key = 'sk-clb-test-"\n$(touch injected)\nCODEX_LB_AUTH'
    endpoint = 'https://example.test/backend-api/codex?quote="&literal=$(pwd)'
    script = build_install_script(platform=platform, api_key=key, base_url=endpoint, model=model)
    env = {**os.environ, "CODEX_HOME": str(codex_dir)}

    for _ in range(2):
        result = subprocess.run(
            ["bash"], input=script, text=True, capture_output=True, env=env, cwd=tmp_path, check=True
        )
        assert key not in result.stdout + result.stderr
    config = tomllib.loads((codex_dir / "config.toml").read_text())
    assert config.get("model") == model
    assert config["model_provider"] == "codex-lb"
    assert config["cli_auth_credentials_store"] == "file"
    assert config["model_providers"]["codex-lb"] == {
        "name": "openai",
        "base_url": endpoint,
        "wire_api": "responses",
        "supports_websockets": True,
        "requires_openai_auth": True,
    }
    assert json.loads((codex_dir / "auth.json").read_text()) == {"OPENAI_API_KEY": key}
    assert (codex_dir / "unrelated.txt").read_text() == "unchanged"
    assert not (tmp_path / "injected").exists()
    for name in ("auth.json", "config.toml"):
        assert stat.S_IMODE((codex_dir / name).stat().st_mode) == 0o600
    backups = list(codex_dir.glob("backup-codex-lb-*"))
    assert len(backups) == 2
    assert any((backup / "config.toml").read_text() == 'model = "old-model"\n' for backup in backups)
    assert any((backup / "auth.json").read_text() == '{"old": "auth"}' for backup in backups)
    assert all(stat.S_IMODE(backup.stat().st_mode) == 0o700 for backup in backups)


def test_installer_defaults_to_user_codex_home(tmp_path: Path) -> None:
    env = {name: value for name, value in os.environ.items() if name != "CODEX_HOME"}
    env["HOME"] = str(tmp_path)
    script = build_install_script(platform="linux", api_key="own-key", base_url="http://localhost", model=None)
    subprocess.run(["bash"], input=script, text=True, capture_output=True, env=env, check=True)
    assert json.loads((tmp_path / ".codex/auth.json").read_text()) == {"OPENAI_API_KEY": "own-key"}


@pytest.mark.parametrize("obstruction", ["symlink", "directory", "backup_failure"])
def test_installer_stops_without_replacing_existing_files(tmp_path: Path, obstruction: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text("original", encoding="utf-8")
    auth = tmp_path / "auth.json"
    if obstruction == "symlink":
        auth.symlink_to(config)
    elif obstruction == "directory":
        auth.mkdir()
    else:
        auth.write_text("original-auth", encoding="utf-8")
    script = build_install_script(platform="linux", api_key="own-key", base_url="http://localhost", model=None)
    if obstruction == "backup_failure":
        script = "function cp() { return 1; }\n" + script
    result = subprocess.run(
        ["bash"], input=script, text=True, capture_output=True, env={**os.environ, "CODEX_HOME": str(tmp_path)}
    )
    assert result.returncode != 0
    assert config.read_text() == "original"
    if obstruction == "backup_failure":
        assert auth.read_text() == "original-auth"


def test_powershell_script_has_literal_data_utf8_output_and_private_acl() -> None:
    script = build_install_script(
        platform="windows", api_key="sk-own-key", base_url="https://test", model="model\n'@\n😀"
    )
    script.encode("ascii")
    config = script.split("$config = @'\n", 1)[1].split("\n'@", 1)[0]
    auth = script.split("$auth = @'\n", 1)[1].split("\n'@", 1)[0]
    assert tomllib.loads(config)["model"] == "model\n'@\n😀"
    assert json.loads(auth) == {"OPENAI_API_KEY": "sk-own-key"}
    assert "[Text.UTF8Encoding]::new($false)" in script
    assert "$fileAcl.SetAccessRuleProtection($true, $false)" in script
    assert script.index("Copy-Item") < script.index("Move-Item")
    assert "Write-Output $auth" not in script

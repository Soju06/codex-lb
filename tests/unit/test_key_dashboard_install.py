from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from app.core.types import JsonValue
from app.modules.key_dashboard.install import InstallPlatform, build_install_script

pytestmark = pytest.mark.unit


def _entry(slug: str = "cd/gpt-6-astra", *, websockets: bool = False) -> dict[str, JsonValue]:
    return {
        "slug": slug,
        "visibility": "list",
        "supported_in_api": True,
        "prefer_websockets": websockets,
        "base_instructions": "Use collaboration tools. Tiếng Việt 😀",
        "multi_agent_version": "v2",
        "supports_parallel_tool_calls": True,
        "experimental_supported_tools": ["namespace"],
        "model_messages": {"instructions_template": "nested metadata {{tools}}"},
    }


@dataclass
class _CatalogServer:
    base_url: str = ""
    payload: JsonValue = field(default_factory=lambda: {"models": [_entry()]})
    status: int = 200
    requests: list[tuple[str, str | None]] = field(default_factory=list)


@pytest.fixture
def catalog_server() -> Iterator[_CatalogServer]:
    state = _CatalogServer()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if state.status == 302:
                self.send_header("Location", state.base_url + "/redirected")
            self.end_headers()
            self.wfile.write(json.dumps(state.payload).encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base_url = f"http://127.0.0.1:{server.server_port}/backend-api/codex"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("platform", ["macos", "linux"])
@pytest.mark.parametrize("model", [None, "gpt-5.6-sol", 'odd "model"\n$(touch injected)\n\'@\nCODEX_LB_CONFIG\n😀'])
def test_installer_writes_safe_private_files_and_recoverable_backups(
    tmp_path: Path, platform: InstallPlatform, model: str | None, catalog_server: _CatalogServer
) -> None:
    codex_dir = tmp_path / "codex home 'quote' $dollar 😀"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text('model = "old-model"\n', encoding="utf-8")
    (codex_dir / "auth.json").write_text('{"old": "auth"}', encoding="utf-8")
    (codex_dir / "codex-lb-models.json").write_text('{"models": []}', encoding="utf-8")
    (codex_dir / "unrelated.txt").write_text("unchanged", encoding="utf-8")
    key = 'sk-clb-test-"$(touch injected)'
    endpoint = catalog_server.base_url
    entry = _entry(model or "cd/gpt-6-astra")
    catalog_server.payload = {"models": [entry, {**_entry("denied"), "visibility": "hide"}]}
    script = build_install_script(platform=platform, api_key=key, base_url=endpoint, model=model)
    env = {**os.environ, "CODEX_HOME": str(codex_dir)}

    for _ in range(2):
        result = subprocess.run(
            ["bash"], input=script, text=True, capture_output=True, env=env, cwd=tmp_path, check=True
        )
        assert key not in result.stdout + result.stderr
    config = tomllib.loads((codex_dir / "config.toml").read_text())
    assert config.get("model") == (model or "cd/gpt-6-astra")
    assert config["model_catalog_json"] == str(codex_dir / "codex-lb-models.json")
    assert json.loads((codex_dir / "codex-lb-models.json").read_text()) == {"models": [entry]}
    assert config["model_provider"] == "codex-lb"
    assert config["cli_auth_credentials_store"] == "file"
    assert config["model_providers"]["codex-lb"] == {
        "name": "openai",
        "base_url": endpoint,
        "wire_api": "responses",
        "supports_websockets": False,
        "requires_openai_auth": True,
    }
    assert json.loads((codex_dir / "auth.json").read_text()) == {"OPENAI_API_KEY": key}
    assert (codex_dir / "unrelated.txt").read_text() == "unchanged"
    assert not (tmp_path / "injected").exists()
    for name in ("auth.json", "config.toml", "codex-lb-models.json"):
        assert stat.S_IMODE((codex_dir / name).stat().st_mode) == 0o600
    backups = list(codex_dir.glob("backup-codex-lb-*"))
    assert len(backups) == 2
    assert any((backup / "config.toml").read_text() == 'model = "old-model"\n' for backup in backups)
    assert any((backup / "auth.json").read_text() == '{"old": "auth"}' for backup in backups)
    assert all(stat.S_IMODE(backup.stat().st_mode) == 0o700 for backup in backups)
    assert any((backup / "codex-lb-models.json").read_text() == '{"models": []}' for backup in backups)
    assert catalog_server.requests == [("/api/key-dashboard/models", f"Bearer {key}")] * 2


def test_installer_defaults_to_user_codex_home(tmp_path: Path, catalog_server: _CatalogServer) -> None:
    env = {name: value for name, value in os.environ.items() if name != "CODEX_HOME"}
    env["HOME"] = str(tmp_path)
    script = build_install_script(platform="linux", api_key="own-key", base_url=catalog_server.base_url, model=None)
    subprocess.run(["bash"], input=script, text=True, capture_output=True, env=env, check=True)
    assert json.loads((tmp_path / ".codex/auth.json").read_text()) == {"OPENAI_API_KEY": "own-key"}


@pytest.mark.parametrize("obstruction", ["symlink", "directory", "backup_failure"])
@pytest.mark.parametrize("target", ["auth.json", "codex-lb-models.json"])
def test_installer_stops_without_replacing_existing_files(tmp_path: Path, obstruction: str, target: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text("original", encoding="utf-8")
    auth = tmp_path / target
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
    setup = script.split("$setupJson = @'\n", 1)[1].split("\n'@", 1)[0]
    assert json.loads(setup)["model"] == "model\n'@\n😀"
    assert tomllib.loads(config)["model_provider"] == "codex-lb"
    assert json.loads(auth) == {"OPENAI_API_KEY": "sk-own-key"}
    assert "[Text.UTF8Encoding]::new($false)" in script
    assert "$fileAcl.SetAccessRuleProtection($true, $false)" in script
    assert script.index("Copy-Item") < script.index("Move-Item")
    assert "Write-Output $auth" not in script
    assert "-MaximumRedirection 0 -TimeoutSec 30" in script
    assert "-Depth 100" in script
    assert "codex-lb-models.json" in script


def test_installer_refreshes_catalog_and_transport(tmp_path: Path, catalog_server: _CatalogServer) -> None:
    script = build_install_script(platform="linux", api_key="own-key", base_url=catalog_server.base_url, model=None)
    env = {**os.environ, "CODEX_HOME": str(tmp_path)}
    native = _entry("native", websockets=True)
    catalog_server.payload = {"models": [native]}
    subprocess.run(["bash"], input=script, text=True, capture_output=True, env=env, check=True)
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["model_providers"]["codex-lb"]["supports_websockets"] is True

    catalog_server.payload = {"models": [native, _entry()]}
    subprocess.run(["bash"], input=script, text=True, capture_output=True, env=env, check=True)
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["model_providers"]["codex-lb"]["supports_websockets"] is False
    assert json.loads((tmp_path / "codex-lb-models.json").read_text()) == catalog_server.payload


@pytest.mark.parametrize(
    "failure", ["unauthorized", "redirect", "shape", "entry", "empty", "hidden", "unsupported", "removed", "bad_key"]
)
def test_catalog_failure_keeps_all_client_files(tmp_path: Path, catalog_server: _CatalogServer, failure: str) -> None:
    for name in ("config.toml", "auth.json", "codex-lb-models.json"):
        (tmp_path / name).write_text("original " + name)
    key = "own-key"
    if failure == "unauthorized":
        catalog_server.status = 401
        catalog_server.payload = {"error": key}
    elif failure == "redirect":
        catalog_server.status = 302
    elif failure == "shape":
        catalog_server.payload = {"data": []}
    elif failure == "entry":
        catalog_server.payload = {"models": [{"slug": "broken"}]}
    elif failure == "empty":
        catalog_server.payload = {"models": []}
    elif failure == "hidden":
        catalog_server.payload = {"models": [{**_entry(), "visibility": "hide"}]}
    elif failure == "unsupported":
        catalog_server.payload = {"models": [{**_entry(), "supported_in_api": False}]}
    elif failure == "bad_key":
        key = "secret\n$(touch injected)\nCODEX_LB_AUTH"
    script = build_install_script(
        platform="linux",
        api_key=key,
        base_url=catalog_server.base_url,
        model="removed" if failure == "removed" else None,
    )
    result = subprocess.run(
        ["bash"],
        input=script,
        text=True,
        capture_output=True,
        env={**os.environ, "CODEX_HOME": str(tmp_path)},
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert key not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "injected").exists()
    for name in ("config.toml", "auth.json", "codex-lb-models.json"):
        assert (tmp_path / name).read_text() == "original " + name
    if failure == "redirect":
        assert len(catalog_server.requests) == 1


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell runtime is unavailable")
@pytest.mark.parametrize("failure", [None, "unauthorized", "redirect", "empty", "removed"])
def test_powershell_catalog_program(tmp_path: Path, catalog_server: _CatalogServer, failure: str | None) -> None:
    model = "custom/'quote' $variable 😀"
    native = _entry("native", websockets=True)
    custom = _entry(model)
    catalog_server.payload = {"models": [native, custom, {**_entry("denied"), "visibility": "hide"}]}
    if failure == "unauthorized":
        catalog_server.status = 401
    elif failure == "redirect":
        catalog_server.status = 302
    elif failure == "empty":
        catalog_server.payload = {"models": []}
    elif failure == "removed":
        model = "removed"
    script = build_install_script(
        platform="windows", api_key="sk-own-key", base_url=catalog_server.base_url, model=model
    )
    script_path = tmp_path / "install.ps1"
    script_path.write_text(script)
    parse = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-Command",
            "$errors = $null; $tokens = $null; "
            "[void][Management.Automation.Language.Parser]::ParseFile("
            "$env:INSTALL_SCRIPT, [ref]$tokens, [ref]$errors); "
            "if ($errors) { $errors; exit 1 }",
        ],
        env={**os.environ, "INSTALL_SCRIPT": str(script_path)},
        text=True,
        capture_output=True,
    )
    assert parse.returncode == 0, parse.stdout + parse.stderr
    # Execute the exact exported catalog program on Linux PowerShell. Windows ACL
    # setup stays outside this test because WindowsIdentity is platform-specific.
    program = (
        "$ErrorActionPreference = 'Stop'\n$codexDir = $env:INSTALL_HOME\n"
        + script[script.index("$config = @'") : script.index("$utf8 =")]
        + "\nConvertTo-Json -InputObject @{ config = $config; catalog = $catalogJson } -Depth 100\n"
    )
    program_path = tmp_path / "catalog.ps1"
    program_path.write_text(program)
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(program_path)],
        env={**os.environ, "INSTALL_HOME": str(tmp_path / "Codex 'quoted' 😀")},
        text=True,
        capture_output=True,
    )
    assert "sk-own-key" not in result.stdout + result.stderr
    if failure:
        assert result.returncode != 0
        if failure == "redirect":
            assert len(catalog_server.requests) == 1
        return
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    config = tomllib.loads(output["config"])
    assert config["model"] == model
    assert config["model_catalog_json"] == str(tmp_path / "Codex 'quoted' 😀/codex-lb-models.json")
    assert config["model_providers"]["codex-lb"]["supports_websockets"] is False
    assert json.loads(output["catalog"]) == {"models": [native, custom]}

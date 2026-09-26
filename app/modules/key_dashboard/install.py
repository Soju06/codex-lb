from __future__ import annotations

import json
from typing import Literal

from app.modules.key_dashboard.install_catalog import UNIX_CATALOG_SETUP, WINDOWS_CATALOG_SETUP
from app.modules.key_dashboard.install_restore import UNIX_RECORD_STATE, UNIX_UNINSTALL_SCRIPT
from app.modules.key_dashboard.install_restore_windows import (
    WINDOWS_PRIVATE_BACKUP,
    WINDOWS_RECORD_STATE,
    WINDOWS_RESTORE_COMMON,
    WINDOWS_UNINSTALL_SCRIPT,
)

InstallPlatform = Literal["macos", "linux", "windows"]


def _toml_string(value: str) -> str:
    # ASCII-only scripts also work with Windows PowerShell 5.1's legacy encoding.
    quoted = json.dumps(value, ensure_ascii=False)
    return "".join(
        char if ord(char) < 127 else f"\\u{ord(char):04x}" if ord(char) <= 0xFFFF else f"\\U{ord(char):08x}"
        for char in quoted
    )


def build_install_script(*, platform: InstallPlatform, api_key: str, base_url: str, model: str | None) -> str:
    """Render credentials as inert file contents, never as interpolated shell code."""
    config = (
        f"openai_base_url = {_toml_string(base_url)}\n"
        'model_provider = "codex-lb"\n'
        'cli_auth_credentials_store = "file"\n\n'
        "[model_providers.codex-lb]\n"
        'name = "openai"\n'
        f"base_url = {_toml_string(base_url)}\n"
        'wire_api = "responses"\n'
        "requires_openai_auth = true\n"
    )
    auth = json.dumps({"OPENAI_API_KEY": api_key}, indent=2) + "\n"
    catalog_url = base_url.removesuffix("/backend-api/codex") + "/api/key-dashboard/models"
    setup = json.dumps({"catalog_url": catalog_url, "model": model}, indent=2) + "\n"
    if platform == "windows":
        return _powershell_script(config, auth, setup)
    return _bash_script(config, auth, setup)


def _bash_script(config: str, auth: str, setup: str) -> str:
    return (
        """#!/usr/bin/env bash
# Configure installed Codex clients. Contains a private API key; do not share.
set -euo pipefail
umask 077
command -v python3 >/dev/null 2>&1 || { printf 'Install Python 3 before running this installer.\\n' >&2; exit 1; }
codex_dir="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$codex_dir"
for name in config.toml auth.json codex-lb-models.json codex-lb-uninstall.sh .codex-lb-install-state.json; do
  if [ -L "$codex_dir/$name" ] || { [ -e "$codex_dir/$name" ] && [ ! -f "$codex_dir/$name" ]; }; then
    printf 'Refusing to replace a symlink or non-file: %s\\n' "$codex_dir/$name" >&2
    exit 1
  fi
done
backup_dir=$(mktemp -d "$codex_dir/backup-codex-lb-$(date +%Y%m%d-%H%M%S)-XXXXXX")
for name in config.toml auth.json codex-lb-models.json codex-lb-uninstall.sh; do
  if [ -f "$codex_dir/$name" ]; then
    cp -p "$codex_dir/$name" "$backup_dir/$name"
  fi
done
cat > "$backup_dir/config.new" <<'CODEX_LB_CONFIG'
"""
        + config
        + """CODEX_LB_CONFIG
cat > "$backup_dir/auth.new" <<'CODEX_LB_AUTH'
"""
        + auth
        + """CODEX_LB_AUTH
cat > "$backup_dir/setup.json" <<'CODEX_LB_SETUP'
"""
        + setup
        + """CODEX_LB_SETUP
python3 - "$codex_dir" "$backup_dir" <<'CODEX_LB_CATALOG'
"""
        + UNIX_CATALOG_SETUP
        + """CODEX_LB_CATALOG
python3 - "$codex_dir" "$backup_dir" <<'CODEX_LB_STATE'
"""
        + UNIX_RECORD_STATE
        + """CODEX_LB_STATE
cat > "$backup_dir/uninstall.new" <<'CODEX_LB_UNINSTALL'
"""
        + UNIX_UNINSTALL_SCRIPT
        + """CODEX_LB_UNINSTALL
for name in config auth catalog state uninstall; do
  chmod 600 "$backup_dir/$name.new"
done
mv -f "$backup_dir/state.new" "$codex_dir/.codex-lb-install-state.json"
mv -f "$backup_dir/uninstall.new" "$codex_dir/codex-lb-uninstall.sh"
mv -f "$backup_dir/catalog.new" "$codex_dir/codex-lb-models.json"
mv -f "$backup_dir/config.new" "$codex_dir/config.toml"
mv -f "$backup_dir/auth.new" "$codex_dir/auth.json"
printf 'Codex configured. Restart your App, CLI or extension. Backup: %s\\n' "$backup_dir"
printf 'Uninstall offline: bash %q\\n' "$codex_dir/codex-lb-uninstall.sh"
"""
    )


def _powershell_script(config: str, auth: str, setup: str) -> str:
    return (
        """# Configure installed Codex clients. Contains a private API key; do not share.
$ErrorActionPreference = 'Stop'
$codexDir = if ($env:CODEX_HOME) { $env:CODEX_HOME } else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'
}
$null = New-Item -ItemType Directory -Force -Path $codexDir
$codexDir = (Get-Item -LiteralPath $codexDir).FullName
"""
        + WINDOWS_RESTORE_COMMON
        + "foreach ($name in $names) { Assert-RegularFile (Join-Path $codexDir $name) }\n"
        + WINDOWS_PRIVATE_BACKUP
        + """foreach ($name in $names) {
    $path = Join-Path $codexDir $name
    if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $backupDir $name) }
}
$config = @'
"""
        + config
        + "'@\n$auth = @'\n"
        + auth
        + "'@\n$setupJson = @'\n"
        + setup
        + "'@\n"
        + WINDOWS_CATALOG_SETUP
        + """$utf8 = [Text.UTF8Encoding]::new($false)
"""
        + WINDOWS_RECORD_STATE
        + "$uninstall = @'\n"
        + WINDOWS_UNINSTALL_SCRIPT
        + "'@\n"
        + """[IO.File]::WriteAllText((Join-Path $backupDir 'uninstall.new'), $uninstall, $utf8)
[IO.File]::WriteAllText((Join-Path $backupDir 'config.new'), $config, $utf8)
[IO.File]::WriteAllText((Join-Path $backupDir 'auth.new'), $auth, $utf8)
[IO.File]::WriteAllText((Join-Path $backupDir 'catalog.new'), $catalogJson, $utf8)
# Protect the new files explicitly; keep unrelated Codex files and ACLs unchanged.
$fileAcl = [Security.AccessControl.FileSecurity]::new()
$fileAcl.SetOwner($sid)
$fileAcl.SetAccessRuleProtection($true, $false)
$fileAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
foreach ($name in @('config', 'auth', 'catalog', 'state', 'uninstall')) {
    Set-Acl -LiteralPath (Join-Path $backupDir ($name + '.new')) -AclObject $fileAcl
}
Move-Item -Force -LiteralPath (Join-Path $backupDir 'state.new') -Destination $statePath
Move-Item -Force -LiteralPath (Join-Path $backupDir 'uninstall.new') `
    -Destination (Join-Path $codexDir 'codex-lb-uninstall.ps1')
Move-Item -Force -LiteralPath (Join-Path $backupDir 'catalog.new') -Destination $catalogPath
Move-Item -Force -LiteralPath (Join-Path $backupDir 'config.new') -Destination (Join-Path $codexDir 'config.toml')
Move-Item -Force -LiteralPath (Join-Path $backupDir 'auth.new') -Destination (Join-Path $codexDir 'auth.json')
Write-Output "Codex configured. Restart your App, CLI or extension. Backup: $backupDir"
$uninstallPath = (Join-Path $codexDir 'codex-lb-uninstall.ps1').Replace("'", "''")
Write-Output "Uninstall offline: powershell -NoProfile -ExecutionPolicy Bypass -File '$uninstallPath'"
"""
    )

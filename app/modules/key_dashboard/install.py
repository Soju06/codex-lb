from __future__ import annotations

import json
from typing import Literal

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
        (f"model = {_toml_string(model)}\n" if model else "") + 'model_provider = "codex-lb"\n'
        'cli_auth_credentials_store = "file"\n\n'
        "[model_providers.codex-lb]\n"
        'name = "openai"\n'
        f"base_url = {_toml_string(base_url)}\n"
        'wire_api = "responses"\n'
        "supports_websockets = true\n"
        "requires_openai_auth = true\n"
    )
    auth = json.dumps({"OPENAI_API_KEY": api_key}, indent=2) + "\n"
    if platform == "windows":
        return _powershell_script(config, auth)
    return _bash_script(config, auth)


def _bash_script(config: str, auth: str) -> str:
    return (
        """#!/usr/bin/env bash
# Configure installed Codex clients. Contains a private API key; do not share.
set -euo pipefail
umask 077
codex_dir="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$codex_dir"
for name in config.toml auth.json; do
  if [ -L "$codex_dir/$name" ] || { [ -e "$codex_dir/$name" ] && [ ! -f "$codex_dir/$name" ]; }; then
    printf 'Refusing to replace a symlink or non-file: %s\\n' "$codex_dir/$name" >&2
    exit 1
  fi
done
backup_dir=$(mktemp -d "$codex_dir/backup-codex-lb-$(date +%Y%m%d-%H%M%S)-XXXXXX")
for name in config.toml auth.json; do
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
chmod 600 "$backup_dir/config.new" "$backup_dir/auth.new"
mv -f "$backup_dir/config.new" "$codex_dir/config.toml"
mv -f "$backup_dir/auth.new" "$codex_dir/auth.json"
printf 'Codex configured. Restart your App, CLI or extension. Backup: %s\\n' "$backup_dir"
"""
    )


def _powershell_script(config: str, auth: str) -> str:
    return (
        """# Configure installed Codex clients. Contains a private API key; do not share.
$ErrorActionPreference = 'Stop'
$codexDir = if ($env:CODEX_HOME) { $env:CODEX_HOME } else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'
}
$null = New-Item -ItemType Directory -Force -Path $codexDir
$codexDir = (Get-Item -LiteralPath $codexDir).FullName
foreach ($name in @('config.toml', 'auth.json')) {
    $path = Join-Path $codexDir $name
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -Force -LiteralPath $path
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing to replace a symlink or non-file: $path"
        }
    }
}
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
$rule = [Security.AccessControl.FileSystemAccessRule]::new(
    $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
$acl.AddAccessRule($rule)
$backupName = 'backup-codex-lb-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N')
$backupDir = Join-Path $codexDir $backupName
$null = New-Item -ItemType Directory -Path $backupDir
Set-Acl -LiteralPath $backupDir -AclObject $acl
foreach ($name in @('config.toml', 'auth.json')) {
    $path = Join-Path $codexDir $name
    if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $backupDir $name) }
}
$config = @'
"""
        + config
        + "'@\n$auth = @'\n"
        + auth
        + """'@
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $backupDir 'config.new'), $config, $utf8)
[IO.File]::WriteAllText((Join-Path $backupDir 'auth.new'), $auth, $utf8)
# Protect the new files explicitly; keep unrelated Codex files and ACLs unchanged.
$fileAcl = [Security.AccessControl.FileSecurity]::new()
$fileAcl.SetOwner($sid)
$fileAcl.SetAccessRuleProtection($true, $false)
$fileAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
foreach ($name in @('config', 'auth')) {
    Set-Acl -LiteralPath (Join-Path $backupDir ($name + '.new')) -AclObject $fileAcl
}
Move-Item -Force -LiteralPath (Join-Path $backupDir 'config.new') -Destination (Join-Path $codexDir 'config.toml')
Move-Item -Force -LiteralPath (Join-Path $backupDir 'auth.new') -Destination (Join-Path $codexDir 'auth.json')
Write-Output "Codex configured. Restart your App, CLI or extension. Backup: $backupDir"
"""
    )

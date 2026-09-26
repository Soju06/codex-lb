"""Offline lifecycle programs for Windows PowerShell 5.1 and later."""

WINDOWS_RESTORE_COMMON = r"""
$names = @('config.toml', 'auth.json', 'codex-lb-models.json', 'codex-lb-uninstall.ps1')
$statePath = Join-Path $codexDir '.codex-lb-install-state.json'
function Assert-RegularFile($path) {
    $item = Get-Item -Force -LiteralPath $path -ErrorAction SilentlyContinue
    if ($null -ne $item -and ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint))) {
        throw "Refusing a symlink or non-file: $path"
    }
}
function Get-Digest($path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Read-InstallState {
    Assert-RegularFile $statePath
    if (-not (Test-Path -LiteralPath $statePath)) { return $null }
    try { $value = [IO.File]::ReadAllText($statePath) | ConvertFrom-Json } catch {
        throw 'Invalid install state; client files were not replaced.'
    }
    if ($null -eq $value -or (($value.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'backup,files,version' `
        -or ($value.version -isnot [long] -and $value.version -isnot [int]) -or $value.version -ne 1 `
        -or $value.backup -isnot [string] -or $value.backup -cnotmatch '\Abackup-codex-lb-[A-Za-z0-9-]+\z' `
        -or $null -eq $value.files `
        -or (($value.files.PSObject.Properties.Name | Sort-Object) -join ',') -cne (($names | Sort-Object) -join ',')) {
        throw 'Invalid install state; client files were not replaced.'
    }
    $original = Join-Path $codexDir $value.backup
    $item = Get-Item -Force -LiteralPath $original -ErrorAction SilentlyContinue
    if ($null -eq $item -or -not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Original backup directory is missing or unsafe.'
    }
    foreach ($name in $names) {
        $path = Join-Path $original $name
        Assert-RegularFile $path
        $expected = $value.files.$name
        if ($null -eq $expected) { $valid = -not (Test-Path -LiteralPath $path) } else {
            $valid = $expected -is [string] -and $expected -cmatch '\A[a-f0-9]{64}\z' `
                -and (Test-Path -LiteralPath $path) -and (Get-Digest $path) -ceq $expected
        }
        if (-not $valid) { throw "Original backup is missing or changed: $name" }
    }
    return $value
}
$state = Read-InstallState
"""

# Used by install and uninstall; no environment or machine-wide ACL changes.
WINDOWS_PRIVATE_BACKUP = r"""
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
"""

WINDOWS_RECORD_STATE = r"""
if ($null -eq $state) {
    $files = [ordered]@{}
    foreach ($name in $names) {
        $path = Join-Path $backupDir $name
        $files[$name] = if (Test-Path -LiteralPath $path) { Get-Digest $path } else { $null }
    }
    $state = [ordered]@{ version = 1; backup = $backupName; files = $files }
}
[IO.File]::WriteAllText((Join-Path $backupDir 'state.new'), ($state | ConvertTo-Json -Depth 5), $utf8)
"""

WINDOWS_UNINSTALL_SCRIPT = (
    r"""# Offline restore for this Codex home. No API key or network required.
$ErrorActionPreference = 'Stop'
$codexDir = $PSScriptRoot
"""
    + WINDOWS_RESTORE_COMMON
    + r"""
if ($null -eq $state) { Write-Output 'No codex-lb install state; nothing to restore.'; return }
foreach ($name in $names) { Assert-RegularFile (Join-Path $codexDir $name) }
"""
    + WINDOWS_PRIVATE_BACKUP
    + r"""
foreach ($name in @($names) + @('.codex-lb-install-state.json')) {
    $path = Join-Path $codexDir $name
    if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $backupDir $name) }
}
$original = Join-Path $codexDir $state.backup
foreach ($name in $names) {
    if ($null -ne $state.files.$name) {
        Copy-Item -LiteralPath (Join-Path $original $name) -Destination (Join-Path $backupDir ($name + '.restore'))
    }
}
foreach ($name in $names) {
    $target = Join-Path $codexDir $name
    if ($null -eq $state.files.$name) {
        if (Test-Path -LiteralPath $target) { Remove-Item -Force -LiteralPath $target }
    } else {
        Move-Item -Force -LiteralPath (Join-Path $backupDir ($name + '.restore')) -Destination $target
    }
}
Remove-Item -Force -LiteralPath $statePath
Write-Output "Original Codex setup restored. Restart your clients. Current-file backup: $backupDir"
"""
)

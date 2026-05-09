[CmdletBinding()]
param(
    [Parameter()]
    [string]$Branch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$DevHost = "codex-usage"
$DevRebuildCommand = "python3 /opt/codex-lb-dev/bin/manage.py rebuild --branch {0}"
$DevStatusCommand = "python3 /opt/codex-lb-dev/bin/manage.py status"
$DevPsCommand = "docker compose --project-directory /opt/codex-lb --file /opt/codex-lb/docker-compose.yml --profile dev ps"
$DevConflictRecoveryCommand = "docker rm -f codex-lb-dev && docker compose --project-directory /opt/codex-lb --file /opt/codex-lb/docker-compose.yml --profile dev up -d codex-lb-dev"
$DevAdminUrl = "https://codex-lb-dev-admin.nosslin.dk/"
$HealthPollSeconds = 60
$HealthPollIntervalSeconds = 3

function Invoke-Git {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $result = & git @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $joined = $Arguments -join " "
        throw "git $joined failed.`n$result"
    }

    return ($result | Out-String).TrimEnd()
}

function Invoke-Ssh {
    param(
        [Parameter(Mandatory)]
        [string]$Command,

        [switch]$AllowFailure
    )

    $escapedCommand = $Command.Replace('"', '\"')
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = "ssh"
    $startInfo.Arguments = "$DevHost `"$escapedCommand`""
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $process.Start() | Out-Null
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    $exitCode = $process.ExitCode

    $outputParts = @()
    if ($stdout) {
        $outputParts += ($stdout | Out-String).TrimEnd()
    }
    if ($stderr) {
        $outputParts += $stderr.TrimEnd()
    }
    $output = ($outputParts -join [Environment]::NewLine).Trim()

    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "ssh $DevHost `"$Command`" failed with exit code $exitCode.`n$output"
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output   = $output
    }
}

function Require-CleanWorkingTree {
    $status = Invoke-Git -Arguments @("status", "--porcelain")
    if ($status) {
        throw "Working tree is dirty. Commit, stash, or discard changes before deploying.`n$status"
    }
}

function Resolve-TargetBranch {
    param(
        [string]$RequestedBranch
    )

    if ($RequestedBranch) {
        Invoke-Git -Arguments @("show-ref", "--verify", "--quiet", "refs/heads/$RequestedBranch") | Out-Null
        return $RequestedBranch
    }

    $currentBranch = Invoke-Git -Arguments @("branch", "--show-current")
    if (-not $currentBranch) {
        throw "Detached HEAD is not supported. Check out a named branch or pass -Branch <name>."
    }

    return $currentBranch
}

function Resolve-UpstreamBranch {
    param(
        [Parameter(Mandatory)]
        [string]$TargetBranch
    )

    $result = & git rev-parse --abbrev-ref "$TargetBranch@{upstream}" 2>&1
    if ($LASTEXITCODE -ne 0) {
        $output = ($result | Out-String).TrimEnd()
        throw "Branch '$TargetBranch' has no upstream tracking branch.`n$output"
    }

    return ($result | Out-String).TrimEnd()
}

function Test-RebuildConflict {
    param(
        [Parameter(Mandatory)]
        [string]$Output
    )

    return $Output -match "codex-lb-dev" -and $Output -match "(already in use|Conflict)"
}

function Wait-ForDevHealthy {
    $deadline = (Get-Date).AddSeconds($HealthPollSeconds)

    do {
        $psResult = Invoke-Ssh -Command $DevPsCommand
        if ($psResult.Output -match "codex-lb-dev\s+.*\(healthy\)") {
            return $psResult.Output
        }

        if ($psResult.Output -notmatch "codex-lb-dev") {
            throw "codex-lb-dev did not appear in docker compose ps output.`n$($psResult.Output)"
        }

        if ($psResult.Output -notmatch "health: starting") {
            throw "codex-lb-dev did not become healthy.`n$($psResult.Output)"
        }

        Start-Sleep -Seconds $HealthPollIntervalSeconds
    } while ((Get-Date) -lt $deadline)

    $finalPs = Invoke-Ssh -Command $DevPsCommand
    throw "Timed out waiting for codex-lb-dev to become healthy.`n$($finalPs.Output)"
}

Require-CleanWorkingTree

$targetBranch = Resolve-TargetBranch -RequestedBranch $Branch
$upstreamBranch = Resolve-UpstreamBranch -TargetBranch $targetBranch

Write-Host "Deploying dev branch: $targetBranch"
Write-Host "Tracking upstream: $upstreamBranch"

Invoke-Git -Arguments @("fetch", "--prune", "--quiet", (Invoke-Git -Arguments @("config", "--get", "branch.$targetBranch.remote")))

$localRef = "refs/heads/$targetBranch"
$aheadBehind = Invoke-Git -Arguments @("rev-list", "--left-right", "--count", "$localRef...$upstreamBranch")
$parts = $aheadBehind -split "\s+"
if ($parts.Count -lt 2) {
    throw "Unable to determine ahead/behind state for '$targetBranch' vs '$upstreamBranch'. Output: $aheadBehind"
}

$ahead = [int]$parts[0]
$behind = [int]$parts[1]
if ($ahead -ne 0 -or $behind -ne 0) {
    throw "Branch '$targetBranch' is out of sync with '$upstreamBranch' (ahead=$ahead, behind=$behind). Push or pull before deploying."
}

$commit = Invoke-Git -Arguments @("rev-parse", "--short", $localRef)
Write-Host "Deploying commit: $commit"

$rebuildCommand = [string]::Format($DevRebuildCommand, $targetBranch)
$rebuildResult = Invoke-Ssh -Command $rebuildCommand -AllowFailure
if ($rebuildResult.ExitCode -ne 0) {
    if (Test-RebuildConflict -Output $rebuildResult.Output) {
        Write-Warning "Detected codex-lb-dev container-name conflict. Running dev-only recovery and retrying rebuild once."
        Invoke-Ssh -Command $DevConflictRecoveryCommand | Out-Null
        $rebuildResult = Invoke-Ssh -Command $rebuildCommand -AllowFailure
    }

    if ($rebuildResult.ExitCode -ne 0) {
        throw "Dev rebuild failed.`n$($rebuildResult.Output)"
    }
}

Write-Host "Rebuild completed successfully."
Write-Host $rebuildResult.Output

$statusResult = Invoke-Ssh -Command $DevStatusCommand
$healthyPsOutput = Wait-ForDevHealthy

Write-Host ""
Write-Host "Dev status:"
Write-Host $statusResult.Output
Write-Host ""
Write-Host "Dev service health:"
Write-Host $healthyPsOutput
Write-Host ""
Write-Host "Dev admin URL: $DevAdminUrl"

import { installScriptPath, type InstallPlatform } from "@/features/key-dashboard/api";

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\"'\"'")}'`;
}

function powershellQuote(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

export function installCommand(platform: InstallPlatform, apiKey: string, origin: string): string {
  const url = new URL(installScriptPath(platform), origin).href;
  const authorization = `Authorization: Bearer ${apiKey}`;
  if (platform === "windows") {
    return `$script = curl.exe -fsS --header ${powershellQuote(authorization)} ${powershellQuote(url)}; if ($LASTEXITCODE -ne 0) { throw 'Download failed' }; & ([ScriptBlock]::Create(($script -join "\`n")))`;
  }
  return `(set -eu; umask 077; installer=$(mktemp); trap 'rm -f "$installer"' EXIT; curl -fsS --header ${shellQuote(authorization)} ${shellQuote(url)} -o "$installer"; bash "$installer")`;
}

export function downloadInstallScript(script: string, platform: InstallPlatform): void {
  const blob = new Blob([script], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `codex-lb-${platform}.${platform === "windows" ? "ps1" : "sh"}`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

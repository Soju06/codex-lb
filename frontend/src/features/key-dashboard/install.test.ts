/// <reference types="node" />

import { execFileSync, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { installCommand } from "@/features/key-dashboard/install";

describe("installer commands", () => {
  it.skipIf(process.platform === "win32")("quotes credentials as data and executes a successfully downloaded script", () => {
    const key = "sk-clb-quote' $(printf INJECTED)";
    const command = installCommand("linux", key, "https://example.test");
    const output = execFileSync("bash", ["-c", `
      curl() {
        printf '%s\\n' "$@";
        printf 'printf SETUP_OK' > "\${!#}";
      }
      ${command}
    `], { encoding: "utf8" });
    expect(output).toContain(`Authorization: Bearer ${key}\n`);
    expect(output).toContain("https://example.test/api/key-dashboard/install-script?platform=linux");
    expect(output).toContain("SETUP_OK");
  });

  it.skipIf(process.platform === "win32")("does not execute a partial or failed download", () => {
    const command = installCommand("macos", "test-key", "https://example.test");
    const result = spawnSync("bash", ["-c", `
      curl() { printf 'printf SHOULD_NOT_RUN' > "\${!#}"; printf '%s' "\${!#}" >&2; return 22; }
      ${command}
    `], { encoding: "utf8" });
    expect(result.status).toBe(22);
    expect(result.stdout).not.toContain("SHOULD_NOT_RUN");
    expect(existsSync(result.stderr)).toBe(false);
  });

  it("uses literal PowerShell quoting and checks curl.exe before execution", () => {
    const command = installCommand("windows", "sk-quote' $variable", "https://example.test");
    expect(command).toContain("'Authorization: Bearer sk-quote'' $variable'");
    expect(command.indexOf("$LASTEXITCODE -ne 0")).toBeLessThan(command.indexOf("[ScriptBlock]::Create"));
    expect(command).toContain('($script -join "`n")');
  });
});

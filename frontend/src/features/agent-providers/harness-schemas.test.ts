import { describe, expect, it } from "vitest";

import {
  AntigravityHarnessPrintResponseSchema,
  AntigravityManagedInteractionRunResponseSchema,
} from "@/features/agent-providers/harness-schemas";

describe("AntigravityHarnessPrintResponseSchema", () => {
  it("parses redacted agy harness output", () => {
    const parsed = AntigravityHarnessPrintResponseSchema.parse({
      providerId: "antigravity",
      accountId: "agy_1",
      externalAccountId: "default",
      command: ["agy", "--print", "--prompt", "<redacted>"],
      cwd: "C:\\repo",
      exitCode: 0,
      stdout: "done",
      stderr: "",
      durationMs: 42,
    });

    expect(parsed.command).toContain("<redacted>");
    expect(parsed.stdout).toBe("done");
  });

  it("parses managed Interactions API run output", () => {
    const parsed = AntigravityManagedInteractionRunResponseSchema.parse({
      providerId: "antigravity",
      agent: "antigravity-preview-05-2026",
      outputText: "done",
      response: { id: "interaction_1", output_text: "done" },
    });

    expect(parsed.agent).toBe("antigravity-preview-05-2026");
    expect(parsed.outputText).toBe("done");
  });
});

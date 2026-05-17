import { describe, expect, it } from "vitest";

import {
  accountStateBadgeVariant,
  formatRunStatusLabel,
  runStatusVariant,
} from "@/features/automations/components/run-status-utils";

describe("run-status-utils", () => {
  it("maps run status variants", () => {
    expect(runStatusVariant("success")).toBe("default");
    expect(runStatusVariant("partial")).toBe("secondary");
    expect(runStatusVariant("failed")).toBe("destructive");
    expect(runStatusVariant("running")).toBe("outline");
  });

  it("maps account state variants including pending", () => {
    expect(accountStateBadgeVariant("pending")).toBe("outline");
    expect(accountStateBadgeVariant("success")).toBe("default");
    expect(accountStateBadgeVariant("partial")).toBe("secondary");
    expect(accountStateBadgeVariant("failed")).toBe("destructive");
    expect(accountStateBadgeVariant("running")).toBe("outline");
  });

  it("formats running label based on pending accounts", () => {
    expect(formatRunStatusLabel("running", 2)).toBe("in progress");
    expect(formatRunStatusLabel("running", 0)).toBe("running");
    expect(formatRunStatusLabel("success", 4)).toBe("success");
  });
});

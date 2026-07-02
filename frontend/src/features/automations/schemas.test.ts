import { describe, expect, it } from "vitest";

import {
  AutomationCreateRequestSchema,
  AutomationJobSchema,
  AutomationRunSchema,
} from "@/features/automations/schemas";

describe("automations schemas", () => {
  it("parses automation job payload", () => {
    const parsed = AutomationJobSchema.parse({
      id: "job_1",
      name: "Daily ping",
      enabled: true,
      schedule: {
        type: "daily",
        time: "05:00",
        timezone: "UTC",
        thresholdMinutes: 0,
        days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
      },
      model: "gpt-5.3-codex",
      prompt: "ping",
      accountIds: ["acc_primary"],
      nextRunAt: "2026-04-15T05:00:00Z",
      lastRun: null,
    });

    expect(parsed.name).toBe("Daily ping");
    expect(parsed.schedule.time).toBe("05:00");
  });

  it("parses automation run payload", () => {
    const parsed = AutomationRunSchema.parse({
      id: "run_1",
      jobId: "job_1",
      trigger: "manual",
      status: "partial",
      scheduledFor: "2026-04-15T05:00:00Z",
      startedAt: "2026-04-15T05:00:00Z",
      finishedAt: "2026-04-15T05:00:02Z",
      accountId: "acc_primary",
      errorCode: null,
      errorMessage: null,
      attemptCount: 2,
    });

    expect(parsed.status).toBe("partial");
    expect(parsed.attemptCount).toBe(2);
  });

  it("accepts create payload with empty accounts list (all accounts mode)", () => {
    const parsed = AutomationCreateRequestSchema.parse({
      name: "Daily ping",
      enabled: true,
      schedule: {
        type: "daily",
        time: "05:00",
        timezone: "UTC",
        thresholdMinutes: 0,
        days: ["mon"],
      },
      model: "gpt-5.3-codex",
      accountIds: [],
    });

    expect(parsed.accountIds).toEqual([]);
  });

  it("rejects duplicate schedule days", () => {
    expect(() =>
      AutomationCreateRequestSchema.parse({
        name: "Daily ping",
        enabled: true,
        schedule: {
          type: "daily",
          time: "05:00",
          timezone: "UTC",
          thresholdMinutes: 0,
          days: ["mon", "mon"],
        },
        model: "gpt-5.3-codex",
        accountIds: ["acc_primary"],
      }),
    ).toThrow();
  });
});

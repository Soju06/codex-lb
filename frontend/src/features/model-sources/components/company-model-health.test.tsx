import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ModelSourceSchema } from "@/features/model-sources/schemas";
import { renderWithProviders } from "@/test/utils";
import { CompanyModelHealth } from "./company-model-health";

function fixture() {
  const date = "2026-09-11T14:00:00Z";
  return ModelSourceSchema.parse({
    id: "src_company", name: "TRAE", kind: "trae", baseUrl: "https://copilot-cn.bytedance.net/api/ide/v2",
    isEnabled: true, healthStatus: "healthy", supportsResponses: true, supportsChatCompletions: false,
    createdAt: date, updatedAt: date, maxConcurrency: 1,
    companyStatus: {
      credentialCache: "present", quotaStatus: "unknown", remaining: null, resetsAt: null, observedUsage: null,
      inFlight: 1,
      healthCheckSummary: { enabledModels: 5, freshChecks: 3, visibleModels: 1, intervalSeconds: 7200, timeoutSeconds: 30, freshnessSeconds: 10800 },
    },
    models: ["healthy", "unhealthy", "slow", "stale", "unknown"].map((state, index) => ({
      id: index, sourceId: "src_company", model: `model-${state}`, createdAt: date, updatedAt: date,
      healthCheck: { state, checkedAt: state === "unknown" ? null : date, expiresAt: null, nextDueAt: null,
        latencyMs: state === "unknown" ? null : 1500, firstTokenMs: null,
        errorCode: state === "unhealthy" ? "company_health_check_timeout" : null,
        inputTokens: state === "healthy" ? 3 : null, outputTokens: state === "healthy" ? 1 : null,
        catalogVisible: state === "healthy" },
    })),
  });
}

describe("Company model health", () => {
  it("separates probe state, catalog visibility and live concurrency", () => {
    renderWithProviders(<CompanyModelHealth source={fixture()} />);
    expect(screen.getByText(/3\/5 enabled models checked within 3h/)).toBeInTheDocument();
    expect(screen.getByText(/Independent probes every 2h/)).toBeInTheDocument();
    expect(screen.getByText(/At concurrency limit/)).toBeInTheDocument();
    const row = screen.getByRole("rowheader", { name: "model-healthy" }).closest("tr")!;
    expect(within(row).getByText("Passed")).toBeInTheDocument();
    expect(within(row).getByText("Shown")).toBeInTheDocument();
    expect(within(row).getByText("3 / 1")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.getByText("Not verified")).toBeInTheDocument();
    expect(screen.getByText("Latency not qualified")).toBeInTheDocument();
    expect(screen.getByText("company_health_check_timeout")).toBeInTheDocument();
    expect(screen.getAllByText("— / —").length).toBeGreaterThan(0);
    expect(screen.getByText(/cross-provider compressed history/)).toBeInTheDocument();
  });

  it("shows source blocking separately from a successful probe", () => {
    const source = fixture();
    source.companyStatus!.budgetExhausted = true;
    source.models[0].healthCheck!.catalogVisible = false;
    renderWithProviders(<CompanyModelHealth source={source} />);
    const row = screen.getByRole("rowheader", { name: "model-healthy" }).closest("tr")!;
    expect(within(row).getByText("Passed")).toBeInTheDocument();
    expect(within(row).getByText("Hidden")).toBeInTheDocument();
    expect(within(row).getByText("Local budget exhausted")).toBeInTheDocument();
  });

  it("does not change non-company source presentation", () => {
    const source = fixture();
    source.companyStatus = null;
    renderWithProviders(<CompanyModelHealth source={source} />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

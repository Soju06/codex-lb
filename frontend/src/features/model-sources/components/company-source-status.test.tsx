import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/utils";
import { ModelSourceSchema } from "@/features/model-sources/schemas";
import { CompanySourceStatus } from "./company-source-status";

describe("company governance controls", () => {
  it("shows cooldown and saves or clears a soft budget", async () => {
    const source = ModelSourceSchema.parse({
      id: "s", name: "Company", kind: "llmbox", baseUrl: "https://example.invalid",
      isEnabled: true, healthStatus: "unavailable", supportsResponses: true, supportsChatCompletions: false,
      createdAt: "2026-09-10T00:00:00Z", updatedAt: "2026-09-10T00:00:00Z", localTokenBudget: 100,
      companyStatus: { credentialCache: "present", quotaStatus: "unknown", remaining: null, resetsAt: null,
        observedUsage: null, health: "unavailable", cooldownUntil: "2026-09-10T01:00:00Z", budgetUsed: 110, budgetExhausted: true },
    });
    const save = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<CompanySourceStatus source={source} disabled={false} onSave={save} />);
    expect(screen.getByText(/Cooling down/)).toBeInTheDocument();
    expect(screen.getByText(/Local budget reached/)).toBeInTheDocument();
    const input = screen.getByRole("spinbutton", { name: "24h token budget" });
    await userEvent.clear(input);
    await userEvent.type(input, "250");
    await userEvent.click(screen.getByRole("button", { name: "Save budget" }));
    expect(save).toHaveBeenLastCalledWith(250);
    await userEvent.clear(input);
    await userEvent.click(screen.getByRole("button", { name: "Save budget" }));
    expect(save).toHaveBeenLastCalledWith(null);
    await userEvent.type(input, "0");
    expect(screen.getByRole("button", { name: "Save budget" })).toBeDisabled();
  });
});

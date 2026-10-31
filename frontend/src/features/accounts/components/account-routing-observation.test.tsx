import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { getAccountRoutingObservation } from "@/features/accounts/api";
import { AccountRoutingObservation } from "@/features/accounts/components/account-routing-observation";

vi.mock("@/features/accounts/api", () => ({ getAccountRoutingObservation: vi.fn() }));

describe("AccountRoutingObservation", () => {
  it("labels replica-local health and does not claim model eligibility", async () => {
    vi.mocked(getAccountRoutingObservation).mockResolvedValue({
      accountId: "observed", scope: "replica_local", healthTier: 1, recentErrors: 3,
      inflightStreams: 2, inflightResponseCreates: 0, lastSelectedAt: null,
      cooldownUntil: "2026-10-31T15:00:00Z", overloadBackoffUntil: null, burstBackoffUntil: null,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AccountRoutingObservation accountId="observed" /></QueryClientProvider>);
    expect(await screen.findByText("draining")).toBeInTheDocument();
    expect(screen.getByText(/This replica only/)).toBeInTheDocument();
    expect(screen.getByText("2026-10-31T15:00:00Z")).toBeInTheDocument();
    expect(getAccountRoutingObservation).toHaveBeenCalledWith("observed");
    client.clear();
  });
});

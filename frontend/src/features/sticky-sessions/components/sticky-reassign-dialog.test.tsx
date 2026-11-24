import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StickyReassignDialog } from "@/features/sticky-sessions/components/sticky-reassign-dialog";
import { listAccounts } from "@/features/accounts/api";
import { reassignStickySession } from "@/features/sticky-sessions/api";
import { createAccountSummary } from "@/test/mocks/factories";

vi.mock("@/features/accounts/api", () => ({ listAccounts: vi.fn() }));
vi.mock("@/features/sticky-sessions/api", () => ({ reassignStickySession: vi.fn() }));

describe("StickyReassignDialog", () => {
  it.each([false, true])("switches a viewed soft owner and shows conflicts: %s", async (conflict) => {
    const user = userEvent.setup();
    vi.mocked(listAccounts).mockResolvedValue({ accounts: [
      createAccountSummary({ accountId: "owner", displayName: "Current" }),
      createAccountSummary({ accountId: "target", displayName: "Available" }),
      createAccountSummary({ accountId: "paused", status: "paused", displayName: "Paused" }),
      createAccountSummary({ accountId: "preserved", routingPolicy: "preserve", displayName: "Preserved" }),
    ] });
    vi.mocked(reassignStickySession).mockReset();
    if (conflict) vi.mocked(reassignStickySession).mockRejectedValue(new Error("Mapping changed; reload"));
    else vi.mocked(reassignStickySession).mockResolvedValue({ status: "reassigned", accountId: "target" });
    const onClose = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><StickyReassignDialog entry={{
      key: "cache-thread", kind: "prompt_cache", accountId: "owner", displayName: "Current",
      createdAt: "2026-10-01T00:00:00Z", updatedAt: "2026-10-01T00:00:00Z", expiresAt: null, isStale: false,
    }} onClose={onClose} /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Target account" })).toBeEnabled());
    await user.click(screen.getByRole("combobox", { name: "Target account" }));
    expect(screen.queryByRole("option", { name: /Paused|Preserved|Current/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: /Available/ }));
    await user.click(screen.getByRole("button", { name: "Switch account" }));
    await waitFor(() => expect(reassignStickySession).toHaveBeenCalledWith({
      key: "cache-thread", kind: "prompt_cache", expectedAccountId: "owner", accountId: "target",
    }));
    if (conflict) {
      expect(await screen.findByText("Mapping changed; reload")).toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
    } else await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    queryClient.clear();
  });
});

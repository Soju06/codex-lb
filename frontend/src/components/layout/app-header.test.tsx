import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AppHeader } from "@/components/layout/app-header";
import { server } from "@/test/mocks/server";
import { createAccountSummary, createDashboardSettings } from "@/test/mocks/factories";

function renderHeader(initialEntry = "/dashboard") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AppHeader onLogout={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppHeader", () => {
  it("shows the summed Accounts reset-credit badge capped at 99+", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({ availableResetCredits: 70 }),
            createAccountSummary({ accountId: "acc-2", availableResetCredits: 40 }),
          ],
        }),
      ),
    );

    renderHeader();

    expect(await screen.findAllByText("99+")).not.toHaveLength(0);
  });

  it("sums reset-credit badge across accounts and treats missing counts as zero", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({ availableResetCredits: 5 }),
            createAccountSummary({ accountId: "acc-2" }),
            createAccountSummary({ accountId: "acc-3", availableResetCredits: null }),
            createAccountSummary({ accountId: "acc-4", availableResetCredits: 3 }),
          ],
        }),
      ),
    );

    renderHeader();

    expect(await screen.findAllByText("8")).not.toHaveLength(0);
  });

  it("hides the Accounts reset-credit badge when no resets are available", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({ availableResetCredits: 0 }),
            createAccountSummary({ accountId: "acc-2", availableResetCredits: 0 }),
          ],
        }),
      ),
    );

    renderHeader();

    await screen.findByRole("link", { name: /Accounts/i });
    expect(screen.queryByText("99+")).not.toBeInTheDocument();
  });

  it("hides the Accounts reset-credit badge when settings disable reset-credit badges", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [createAccountSummary({ availableResetCredits: 5 })],
        }),
      ),
      http.get("/api/settings", () =>
        HttpResponse.json(createDashboardSettings({ showResetCreditBadges: false })),
      ),
    );

    renderHeader();

    await screen.findByRole("link", { name: /Accounts/i });
    await waitFor(() => {
      expect(screen.queryByText("5")).not.toBeInTheDocument();
    });
  });

  it("renders core destinations as top-level links and keeps Automations out of the pill bar", async () => {
    renderHeader();

    expect(await screen.findByRole("link", { name: /Dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Reports/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Accounts/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /APIs/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Automations" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Advanced" })).toBeInTheDocument();
  });

  it("reveals Automations after opening the Advanced menu", async () => {
    const user = userEvent.setup();
    renderHeader();

    await user.click(screen.getByRole("button", { name: "Advanced" }));

    expect(await screen.findByRole("menuitem", { name: "Automations" })).toBeInTheDocument();
  });

  it("marks the Advanced trigger active only while an advanced route is current", () => {
    renderHeader("/automations");
    expect(screen.getByRole("button", { name: "Advanced" })).toHaveAttribute("data-active", "true");
  });

  it("keeps the Advanced trigger inactive on core routes", () => {
    renderHeader("/dashboard");
    expect(screen.getByRole("button", { name: "Advanced" })).toHaveAttribute("data-active", "false");
  });
});

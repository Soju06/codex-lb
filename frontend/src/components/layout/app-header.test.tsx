import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AppHeader } from "@/components/layout/app-header";
import { server } from "@/test/mocks/server";
import { createAccountSummary } from "@/test/mocks/factories";

function renderHeader() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard"]}>
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

    await screen.findByRole("link", { name: /^Accounts$/i });
    expect(screen.queryByText("99+")).not.toBeInTheDocument();
  });
});

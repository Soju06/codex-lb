import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { createDashboardAuthSession, createSessionUser } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

// A signed-in account whose role lacks `accounts:read`: the Accounts nav item
// disappears and its route falls back to the dashboard.
const dashboardOnlySession = createDashboardAuthSession({
  permissions: ["read", "dashboard:read:all"],
  user: createSessionUser({ username: "viewer", role: { id: "r3", slug: "custom", name: "Reader", kind: "custom" } }),
});

describe("permission-aware navigation integration", () => {
  it("redirects a guarded route the session cannot use to the dashboard and hides its nav item", async () => {
    server.use(http.get("/api/dashboard-auth/session", () => HttpResponse.json(dashboardOnlySession)));

    window.history.pushState({}, "", "/accounts");
    renderWithProviders(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/dashboard"));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Accounts/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Reports/i })).toBeInTheDocument();
  });
});

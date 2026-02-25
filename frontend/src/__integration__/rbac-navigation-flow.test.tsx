import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/mocks/server";

describe("rbac navigation flow", () => {
  it("admin can access users page and sees users navigation", async () => {
    server.use(
      http.get("/api/dashboard-auth/session", () =>
        HttpResponse.json({
          authenticated: true,
          passwordRequired: true,
          totpRequiredOnLogin: false,
          totpConfigured: true,
          user: {
            id: "dashboard-user-admin-default",
            username: "admin",
            role: "admin",
          },
        }),
      ),
    );

    window.history.pushState({}, "", "/users");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
  });

  it("user cannot access users page and is redirected to dashboard", async () => {
    server.use(
      http.get("/api/dashboard-auth/session", () =>
        HttpResponse.json({
          authenticated: true,
          passwordRequired: true,
          totpRequiredOnLogin: false,
          totpConfigured: true,
          user: {
            id: "dashboard-user-1",
            username: "alice",
            role: "user",
          },
        }),
      ),
    );

    window.history.pushState({}, "", "/users");
    renderWithProviders(<App />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });
});

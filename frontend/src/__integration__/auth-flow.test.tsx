import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/mocks/server";

describe("auth flow integration", () => {
  it("flows from login to totp to dashboard", async () => {
    const user = userEvent.setup({ delay: null });

    server.use(
      http.get("/api/dashboard-auth/session", () =>
        HttpResponse.json({
          authenticated: false,
          passwordRequired: true,
          totpRequiredOnLogin: false,
          totpConfigured: true,
        }),
      ),
      http.post("/api/dashboard-auth/password/login", () =>
        HttpResponse.json({
          authenticated: false,
          passwordRequired: true,
          totpRequiredOnLogin: true,
          totpConfigured: true,
        }),
      ),
      http.post("/api/dashboard-auth/totp/verify", () =>
        HttpResponse.json({
          authenticated: true,
          passwordRequired: true,
          totpRequiredOnLogin: false,
          totpConfigured: true,
        }),
      ),
    );

    window.history.pushState({}, "", "/dashboard");
    renderWithProviders(<App />);

    expect(await screen.findByText("Sign in")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Username"));
    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "secret-password");
    await user.click(screen.getByRole("button", { name: "Sign In" }));

    expect(await screen.findByText("Two-factor verification")).toBeInTheDocument();

    await user.type(screen.getByLabelText("TOTP code"), "123456");

    // Auto-submit triggers on 6-digit completion via onComplete
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    });
  });
});

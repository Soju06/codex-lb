import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import App from "@/App";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { createDashboardAuthSession } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

describe("/auth/pending with the real routes", () => {
  afterEach(() => {
    window.history.pushState({}, "", "/");
    useAuthStore.setState({ ...useAuthStore.getInitialState(), initialized: false });
  });

  it("shows the pending screen to a refused proxy identity and stays on the route", async () => {
    server.use(
      http.get("/api/dashboard-auth/session", () =>
        HttpResponse.json(
          createDashboardAuthSession({
            authenticated: false,
            passwordRequired: true,
            authMode: "trusted_header",
            role: "guest",
            permissions: [],
            login: { usernameField: "shown", providers: [], localLogin: "enabled", pendingIdentity: true },
          }),
        ),
      ),
    );
    window.history.pushState({}, "", "/auth/pending");

    renderWithProviders(<App />);

    expect(await screen.findByText("Your account is not ready yet")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/auth/pending");
  });

  it("sends a signed-in account from /auth/pending into the app", async () => {
    window.history.pushState({}, "", "/auth/pending");

    renderWithProviders(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/dashboard"));
    expect(screen.queryByText("Your account is not ready yet")).not.toBeInTheDocument();
  });
});

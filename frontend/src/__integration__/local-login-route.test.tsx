import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import App from "@/App";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { createDashboardAuthSession } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

const PROXY = { kind: "trusted_header", providerKey: "default", label: "Reverse proxy", loginUrl: null };

function restrictedSession() {
  server.use(
    http.get("/api/dashboard-auth/session", () =>
      HttpResponse.json(
        createDashboardAuthSession({
          authenticated: false,
          passwordRequired: true,
          role: "guest",
          permissions: [],
          login: {
            usernameField: "shown",
            providers: [{ kind: "password", providerKey: "default", label: "Password", loginUrl: null }, PROXY],
            localLogin: "break_glass_only",
            pendingIdentity: false,
          },
        }),
      ),
    ),
  );
}

describe("/login with the real routes", () => {
  afterEach(() => {
    window.history.pushState({}, "", "/");
    useAuthStore.setState({ ...useAuthStore.getInitialState(), initialized: false });
  });

  it("keeps the password form off the front door while break_glass_only is in force", async () => {
    restrictedSession();
    window.history.pushState({}, "", "/");

    renderWithProviders(<App />);

    await screen.findByTestId("login-providers");
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("opens the form at /login?local=1 and stays on that URL", async () => {
    restrictedSession();
    window.history.pushState({}, "", "/login?local=1");

    renderWithProviders(<App />);

    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });

  it("sends a signed-in session from /login into the app instead of the not-found page", async () => {
    window.history.pushState({}, "", "/login?local=1");

    renderWithProviders(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/dashboard"));
    expect(screen.queryByText("Page not found")).not.toBeInTheDocument();
  });
});

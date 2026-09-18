import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import App from "@/App";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { rememberFlow } from "@/features/auth/oidc-window";
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
            pendingArrival: null,
          },
        }),
      ),
    ),
  );
}

describe("/login with the real routes", () => {
  afterEach(() => {
    window.history.pushState({}, "", "/");
    window.sessionStorage.clear();
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

  // The server ends a failed sign-in flow at the login screen. For a pre-flight
  // the browser refused a window to, that lands the still-signed-in operator
  // there in the tab they started from, and the dashboard is a page away from
  // the card that is waiting to tell them what happened.
  it("returns a same-tab sign-in flow that failed to the card that started it", async () => {
    rememberFlow({ providerId: "provider_oidc", purpose: "test-login", verifiedAt: null });
    window.history.pushState({}, "", "/login?sso=failed");

    renderWithProviders(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/settings"));
    expect(`${window.location.search}${window.location.hash}`).toBe("?org=1#oidc");
  });
});

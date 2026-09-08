import { HttpResponse, http } from "msw";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import App from "@/App";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { createAccountSummary, createDashboardAuthSession } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

// Reads the backend answers with 403 `permission_required` for principals
// without the coarse `write` alias (the built-in guest). The guest UI must
// never issue them.
const RESTRICTED_PATHS = [
  "/api/api-keys/",
  "/api/api-keys",
  "/api/settings/upstream-proxy",
  "/api/settings/runtime/connect-address",
  "/api/sticky-sessions",
  "/api/oauth/status",
];

const guestSession = createDashboardAuthSession({
  authenticated: true,
  passwordRequired: false,
  totpConfigured: false,
  role: "guest",
  permissions: ["read"],
  guestAccessEnabled: true,
  guestPasswordRequired: false,
});

const maskedAccounts = [
  createAccountSummary({
    accountId: "acc_primary",
    email: "p***@example.com",
    displayName: "p***@example.com",
    chatgptAccountId: null,
    workspaceId: null,
    workspaceLabel: null,
  }),
];

function spyRequestPaths(): string[] {
  const paths: string[] = [];
  server.events.on("request:start", ({ request }) => {
    paths.push(new URL(request.url).pathname);
  });
  return paths;
}

function restrictedRequests(paths: string[]): string[] {
  return paths.filter((path) => RESTRICTED_PATHS.includes(path) || path.startsWith("/api/api-keys/"));
}

function useGuestSession() {
  server.use(
    http.get("/api/dashboard-auth/session", () => HttpResponse.json(guestSession)),
    http.get("/api/accounts", () => HttpResponse.json({ accounts: maskedAccounts })),
  );
  useAuthStore.setState({
    authenticated: true,
    passwordRequired: false,
    role: "guest",
    permissions: ["read"],
    canWrite: false,
    guestAccessEnabled: true,
    guestPasswordRequired: false,
    initialized: true,
  });
}

describe("guest restricted surfaces integration", () => {
  beforeEach(() => {
    useAuthStore.setState({ role: "admin", permissions: ["read", "write"], canWrite: true, initialized: false });
  });

  afterEach(() => {
    server.events.removeAllListeners();
    useAuthStore.setState({ role: "admin", permissions: ["read", "write"], canWrite: true, initialized: false });
  });

  it("shows the administrator-only notice on /apis without requesting API keys", async () => {
    useGuestSession();
    const paths = spyRequestPaths();
    window.history.pushState({}, "", "/apis");

    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "APIs" })).toBeInTheDocument();
    expect(await screen.findByText("API keys are managed by administrators")).toBeInTheDocument();
    expect(screen.getByText("Sign in as an administrator to view and manage API keys.")).toBeInTheDocument();
    // Header badge query proves the page settled its network work.
    await waitFor(() => expect(paths).toContain("/api/accounts"));

    expect(screen.queryByRole("button", { name: "Create API Key" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(restrictedRequests(paths)).toEqual([]);
  });

  it("does not mount or fetch API key, upstream-proxy, or sticky-session surfaces on /settings", async () => {
    useGuestSession();
    const paths = spyRequestPaths();
    window.history.pushState({}, "", "/settings?advanced=1");

    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(
      await screen.findByText(
        "You are viewing the dashboard with read-only guest access. Admin controls are disabled.",
      ),
    ).toBeInTheDocument();
    // Advanced is expanded via the deep link; allowed self-fetching sections still load.
    await waitFor(() => expect(paths).toContain("/api/firewall/ips"));
    await waitFor(() => expect(paths).toContain("/api/model-sources/"));

    expect(screen.queryByRole("button", { name: "Create key" })).not.toBeInTheDocument();
    expect(screen.queryByText("API Keys")).not.toBeInTheDocument();
    expect(screen.queryByText("Sticky sessions")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(restrictedRequests(paths)).toEqual([]);
  });

  it("renders masked accounts, hides OAuth help, and skips the upstream-proxy query on /accounts", async () => {
    useGuestSession();
    const paths = spyRequestPaths();
    window.history.pushState({}, "", "/accounts");

    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Accounts" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "p***@example.com" })).toBeInTheDocument();
    await waitFor(() => expect(paths).toContain("/api/accounts/acc_primary/usage-reset-credits"));

    expect(screen.getAllByText(/Personal \/ unknown workspace/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Need help?" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add account" })).toBeDisabled();
    expect(restrictedRequests(paths)).toEqual([]);
  });

  it("keeps requesting the restricted reads for writers (regression)", async () => {
    const paths = spyRequestPaths();
    window.history.pushState({}, "", "/settings?advanced=1");

    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    await waitFor(() => expect(paths).toContain("/api/api-keys/"));
    await waitFor(() => expect(paths).toContain("/api/settings/upstream-proxy"));
    await waitFor(() => expect(paths).toContain("/api/sticky-sessions"));
    expect(await screen.findByRole("button", { name: "Create key" })).toBeInTheDocument();
  });

  it("keeps the API key page and accounts help for writers (regression)", async () => {
    const paths = spyRequestPaths();
    window.history.pushState({}, "", "/apis");

    renderWithProviders(<App />);

    expect(await screen.findByRole("button", { name: "Create API Key" })).toBeInTheDocument();
    await waitFor(() => expect(paths).toContain("/api/api-keys/"));
    expect(screen.queryByText("API keys are managed by administrators")).not.toBeInTheDocument();
  });
});

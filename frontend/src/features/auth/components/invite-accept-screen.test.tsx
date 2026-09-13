import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "@/features/auth/components/auth-gate";
import { InviteAcceptScreen } from "@/features/auth/components/invite-accept-screen";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import type { InviteDescription } from "@/features/auth/schemas";
import { MOCK_INVITE_TOKEN } from "@/test/mocks/handlers";
import {
  OPERATOR_PERMISSIONS,
  createDashboardAuthSession,
  createSessionUser,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

const inviteDescription: InviteDescription = {
  roleName: "Operator",
  inviterDisplayName: "admin",
  suggestedUsername: "sarah",
  usernameLocked: false,
  expiresAt: "2026-02-01T18:00:00Z",
};

const visitorSession = createDashboardAuthSession({ authenticated: false, permissions: [], user: null });
const acceptedSession = (overrides = {}) =>
  createDashboardAuthSession({
    permissions: OPERATOR_PERMISSIONS,
    user: createSessionUser({ username: "sarah", role: { id: "r2", slug: "operator", name: "Operator", kind: "preset" } }),
    ...overrides,
  });

function useInvite(overrides: Partial<InviteDescription> = {}) {
  server.use(
    http.get("/api/dashboard-auth/invite/:token", () => HttpResponse.json({ ...inviteDescription, ...overrides })),
  );
}

async function fillPasswords(user: ReturnType<typeof userEvent.setup>, password = "strong-password") {
  await user.type(screen.getByLabelText("Password"), password);
  await user.type(screen.getByLabelText("Confirm password"), password);
  await user.click(screen.getByRole("button", { name: "Create account and sign in" }));
}

describe("InviteAcceptScreen", () => {
  beforeEach(() => {
    useAuthStore.setState({
      ...useAuthStore.getInitialState(),
      initialized: true,
      passwordRequired: true,
      loginHint: { usernameField: "shown", providers: [], localLogin: "enabled", pendingIdentity: false },
    });
    server.use(http.get("/api/dashboard-auth/session", () => HttpResponse.json(visitorSession)));
  });

  it("checks the token first, then lets the person create their sign-in and applies the returned session", async () => {
    const user = userEvent.setup();
    let acceptBody: unknown = null;
    server.use(
      http.post("/api/dashboard-auth/invite/accept", async ({ request }) => {
        acceptBody = await request.json();
        return HttpResponse.json(acceptedSession());
      }),
    );

    renderWithProviders(<InviteAcceptScreen token={MOCK_INVITE_TOKEN} />);

    expect(screen.getByText("Checking your invite…")).toBeInTheDocument();
    expect(await screen.findByText("admin invited you to join as Operator.")).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toHaveValue("sarah");

    await fillPasswords(user);

    await waitFor(() => expect(window.location.pathname).toBe("/"));
    expect(acceptBody).toEqual({ token: MOCK_INVITE_TOKEN, username: "sarah", password: "strong-password" });
    const state = useAuthStore.getState();
    expect(state.authenticated).toBe(true);
    expect(state.user?.username).toBe("sarah");
    expect(state.tier).toBe("team");
  });

  it("rejects mismatched passwords and malformed usernames before calling the server", async () => {
    const user = userEvent.setup();
    useInvite();
    const accept = vi.fn();
    server.use(http.post("/api/dashboard-auth/invite/accept", accept));

    renderWithProviders(<InviteAcceptScreen token={MOCK_INVITE_TOKEN} />);
    await screen.findByLabelText("Username");

    await user.type(screen.getByLabelText("Password"), "strong-password");
    await user.type(screen.getByLabelText("Confirm password"), "different-pass");
    await user.click(screen.getByRole("button", { name: "Create account and sign in" }));
    expect(await screen.findByText("Passwords do not match.")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Username"));
    await user.type(screen.getByLabelText("Username"), "sarah smith");
    await user.click(screen.getByRole("button", { name: "Create account and sign in" }));
    expect(await screen.findByText("Use letters, numbers, dots, underscores or hyphens.")).toBeInTheDocument();
    expect(accept).not.toHaveBeenCalled();
  });

  it("shows the single expiry message for a 404 and nothing else", async () => {
    renderWithProviders(<InviteAcceptScreen token="stale-token" />);

    expect(
      await screen.findByText("This invite has expired. Ask the person who invited you for a new link."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
    expect(screen.queryByText(/Operator/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("shows the server message with a retry for a rate-limited lookup, and retries", async () => {
    const user = userEvent.setup();
    let lookups = 0;
    server.use(
      http.get("/api/dashboard-auth/invite/:token", () => {
        lookups += 1;
        if (lookups === 1) {
          return HttpResponse.json(
            { error: { code: "invite_rate_limited", message: "Too many attempts. Try again in 30 seconds." } },
            { status: 429 },
          );
        }
        return HttpResponse.json(inviteDescription);
      }),
    );

    renderWithProviders(<InviteAcceptScreen token={MOCK_INVITE_TOKEN} />);

    expect(await screen.findByText("Too many attempts. Try again in 30 seconds.")).toBeInTheDocument();
    expect(screen.queryByText(/This invite has expired/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByLabelText("Username")).toHaveValue("sarah");
    expect(lookups).toBe(2);
  });

  it("tells a signed-in account to log out and offers the button", async () => {
    const user = userEvent.setup();
    const logout = vi.fn().mockResolvedValue(undefined);
    useAuthStore.setState({ authenticated: true, user: createSessionUser({ username: "admin" }), logout });

    renderWithProviders(<InviteAcceptScreen token={MOCK_INVITE_TOKEN} />);

    expect(
      await screen.findByText("You are currently signed in as admin. Log out to accept this invite."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Logout" }));
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("treats a password session still pending TOTP as signed in (the server refuses it too)", async () => {
    useAuthStore.setState({ authenticated: false, totpRequiredOnLogin: true, user: createSessionUser({ username: "admin" }) });

    renderWithProviders(<InviteAcceptScreen token={MOCK_INVITE_TOKEN} />);

    expect(
      await screen.findByText("You are currently signed in as admin. Log out to accept this invite."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("switches to the signed-in branch when the server answers already_signed_in", async () => {
    const user = userEvent.setup();
    useInvite();
    server.use(
      http.post("/api/dashboard-auth/invite/accept", () =>
        HttpResponse.json(
          { error: { code: "already_signed_in", message: "Sign out before accepting an invite" } },
          { status: 409 },
        ),
      ),
      // The cookie session the client did not know about yet.
      http.get("/api/dashboard-auth/session", () =>
        HttpResponse.json(createDashboardAuthSession({ user: createSessionUser({ username: "admin" }) })),
      ),
    );

    renderWithProviders(<InviteAcceptScreen token={MOCK_INVITE_TOKEN} />);
    await screen.findByLabelText("Username");
    await fillPasswords(user);

    expect(
      await screen.findByText("You are currently signed in as admin. Log out to accept this invite."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Logout" })).toBeInTheDocument();
    expect(screen.queryByText("Sign out before accepting an invite")).not.toBeInTheDocument();
  });

  it("keeps a locked username read-only and sends the suggested one", async () => {
    const user = userEvent.setup();
    useInvite({ usernameLocked: true, inviterDisplayName: null });
    let acceptBody: { username?: string } | null = null;
    server.use(
      http.post("/api/dashboard-auth/invite/accept", async ({ request }) => {
        acceptBody = (await request.json()) as { username?: string };
        return HttpResponse.json(acceptedSession());
      }),
    );

    renderWithProviders(<InviteAcceptScreen token={MOCK_INVITE_TOKEN} />);

    expect(await screen.findByText("You were invited to join as Operator.")).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toHaveAttribute("readonly");
    expect(screen.getByText("Your username was chosen by the person who invited you.")).toBeInTheDocument();

    await fillPasswords(user);

    await waitFor(() => expect(acceptBody?.username).toBe("sarah"));
  });

  it("surfaces a taken username inline", async () => {
    const user = userEvent.setup();
    useInvite();
    server.use(
      http.post("/api/dashboard-auth/invite/accept", () =>
        HttpResponse.json({ error: { code: "username_taken", message: "Username is taken" } }, { status: 409 }),
      ),
    );

    renderWithProviders(<InviteAcceptScreen token={MOCK_INVITE_TOKEN} />);
    await screen.findByLabelText("Username");
    await fillPasswords(user);

    expect(await screen.findByText("That username is already taken.")).toBeInTheDocument();
  });

  it("hands an enrollment-required session to the gate, which renders the authenticator setup", async () => {
    const user = userEvent.setup();
    useInvite();
    server.use(
      http.post("/api/dashboard-auth/invite/accept", () =>
        HttpResponse.json(acceptedSession({ totpConfigured: false, totpEnrollmentRequired: true })),
      ),
    );
    useAuthStore.setState({ initialized: false });

    window.history.pushState({}, "", `/invite/${MOCK_INVITE_TOKEN}`);
    renderWithProviders(
      <AuthGate>
        <div>Protected content</div>
      </AuthGate>,
    );

    await screen.findByLabelText("Username");
    await fillPasswords(user);

    expect(await screen.findByText("Set up two-factor authentication")).toBeInTheDocument();
    expect(await screen.findByText("Secret: JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });
});

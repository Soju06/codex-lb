import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { LoginHintSchema } from "@/features/auth/schemas";
import { createSessionUser } from "@/test/mocks/factories";

vi.mock("@/features/auth/components/totp-dialog", () => ({
  TotpDialog: () => <div>Two-factor verification</div>,
}));

vi.mock("@/features/auth/components/invite-accept-screen", () => ({
  InviteAcceptScreen: ({ token }: { token: string }) => <div>Invite screen for {token}</div>,
}));

vi.mock("@/features/auth/components/totp-enrollment-form", () => ({
  TotpEnrollmentForm: ({ onEnrolled }: { onEnrolled: (code: string) => void }) => (
    <button type="button" onClick={() => onEnrolled("123456")}>
      Enrollment form
    </button>
  ),
}));

function setAuthState(
  patch: Partial<ReturnType<typeof useAuthStore.getState>>,
): void {
  useAuthStore.setState({
    initialized: true,
    loading: false,
    passwordRequired: true,
    authenticated: false,
    totpRequiredOnLogin: false,
    bootstrapRequired: false,
    bootstrapTokenConfigured: false,
    authMode: "standard",
    passwordManagementEnabled: true,
    totpEnrollmentRequired: false,
    user: null,
    adminLoginRequested: false,
    guestAccessEnabled: false,
    guestPasswordRequired: false,
    localPasswordConfigured: false,
    loginHint: LoginHintSchema.parse({}),
    error: null,
    ...patch,
  });
}

function renderGate(initialEntry = "/dashboard") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthGate>
        <div>Protected content</div>
      </AuthGate>
    </MemoryRouter>,
  );
}

describe("AuthGate", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setAuthState({
      refreshSession: vi.fn().mockResolvedValue(undefined),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("hides children and shows the spinner until the session resolves", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      initialized: false,
      loading: false,
      passwordRequired: false,
      authenticated: false,
    });

    renderGate();

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    expect(screen.queryByText("Sign in")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("shows login form when unauthenticated", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: false,
      totpRequiredOnLogin: false,
    });

    renderGate();

    expect(screen.getByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("shows guest login prompt when guest access requires a password", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: false,
      authenticated: false,
      guestAccessEnabled: true,
      guestPasswordRequired: true,
    });

    renderGate();

    expect(screen.getByText("Guest access")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("shows children when authenticated", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: true,
      totpRequiredOnLogin: false,
    });

    renderGate();

    expect(screen.getByText("Protected content")).toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("shows admin login form when a guest requests admin sign in", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: true,
      role: "guest",
      adminLoginRequested: true,
    });

    renderGate();

    expect(screen.getByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("shows totp dialog when verification is pending", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: false,
      totpRequiredOnLogin: true,
    });

    renderGate();

    expect(screen.getByText("Two-factor verification")).toBeInTheDocument();
    expect(screen.queryByText("Dashboard Login")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("shows reverse proxy notice when trusted header auth is required", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: false,
      authenticated: false,
      totpRequiredOnLogin: false,
      authMode: "trusted_header",
    });

    renderGate();

    expect(screen.getByText("Reverse proxy authentication required")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("shows reverse proxy notice instead of guest login in trusted header mode", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: false,
      authenticated: false,
      totpRequiredOnLogin: false,
      authMode: "trusted_header",
      guestAccessEnabled: true,
      guestPasswordRequired: true,
    });

    renderGate();

    expect(screen.getByText("Reverse proxy authentication required")).toBeInTheDocument();
    expect(screen.queryByText("Sign in")).not.toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("parks a proxy identity without an account on the pending screen and retries from there", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: false,
      authMode: "trusted_header",
      loginHint: { usernameField: "shown", providers: [], localLogin: "enabled", pendingIdentity: true },
    });

    renderGate();

    expect(screen.getByText("Your account is not ready yet")).toBeInTheDocument();
    expect(screen.getByTestId("pending-identity")).toHaveTextContent("Ask an administrator to add you");
    expect(screen.queryByText("Sign in")).not.toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));

    screen.getByRole("button", { name: "Try again" }).click();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(2));
  });

  it("renders the pending screen on /auth/pending for a visitor and the app once signed in", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({ refreshSession, passwordRequired: true, authenticated: false, authMode: "trusted_header" });

    const view = renderGate("/auth/pending");
    expect(screen.getByText("Your account is not ready yet")).toBeInTheDocument();
    expect(screen.queryByText("Sign in")).not.toBeInTheDocument();
    view.unmount();

    setAuthState({ refreshSession, passwordRequired: true, authenticated: true, user: createSessionUser() });
    renderGate("/auth/pending");
    expect(screen.getByText("Protected content")).toBeInTheDocument();
  });

  it("keeps the URL-only pending clause to trusted-header installs", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({ refreshSession, passwordRequired: true, authenticated: false, authMode: "standard" });

    renderGate("/auth/pending");

    expect(screen.getByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByText("Your account is not ready yet")).not.toBeInTheDocument();
  });

  it("does not pull an invite link onto the pending screen", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: false,
      authMode: "trusted_header",
      loginHint: { usernameField: "shown", providers: [], localLogin: "enabled", pendingIdentity: true },
    });

    renderGate("/invite/abc123");

    expect(screen.getByText("Invite screen for abc123")).toBeInTheDocument();
    expect(screen.queryByText("Your account is not ready yet")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("offers the local password login on the pending screen only when a password account exists", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    const pending = {
      usernameField: "shown" as const,
      providers: [{ kind: "password", providerKey: "default", label: "Password", loginUrl: null }],
      localLogin: "enabled" as const,
      pendingIdentity: true,
    };
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: false,
      authMode: "trusted_header",
      localPasswordConfigured: false,
      loginHint: pending,
    });
    const view = renderGate();
    expect(screen.queryByTestId("pending-local-login")).not.toBeInTheDocument();
    view.unmount();

    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: false,
      authMode: "trusted_header",
      localPasswordConfigured: true,
      loginHint: pending,
    });
    renderGate();
    expect(screen.getByTestId("pending-local-login")).toBeInTheDocument();
    expect(screen.getByText("Your account is not ready yet")).toBeInTheDocument();
    expect(screen.getByText("Sign in")).toBeInTheDocument();
  });

  it("shows bootstrap setup screen for remote first-run access", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: false,
      authenticated: false,
      bootstrapRequired: true,
      bootstrapTokenConfigured: true,
    });

    renderGate();

    expect(screen.getByText("Complete Remote Setup")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set password" })).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("renders the public invite screen before the login branch for a visitor", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({ refreshSession, passwordRequired: true, authenticated: false });

    renderGate("/invite/abc123");

    expect(screen.getByText("Invite screen for abc123")).toBeInTheDocument();
    expect(screen.queryByText("Sign in")).not.toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("renders the public invite screen for a signed-in account too", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({ refreshSession, passwordRequired: true, authenticated: true, user: createSessionUser() });

    renderGate("/invite/abc123");

    expect(screen.getByText("Invite screen for abc123")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("holds a signed-in account at the authenticator enrollment step", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: true,
      totpEnrollmentRequired: true,
      user: createSessionUser(),
    });

    renderGate();

    expect(screen.getByText("Set up two-factor authentication")).toBeInTheDocument();
    expect(screen.getByText("Enrollment form")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Logout" })).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });

  it("verifies the confirmed code right after enrollment so the TOTP dialog is not shown twice", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    const verifyTotp = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      verifyTotp,
      passwordRequired: true,
      authenticated: true,
      totpEnrollmentRequired: true,
      user: createSessionUser(),
    });

    renderGate();
    screen.getByRole("button", { name: "Enrollment form" }).click();

    await waitFor(() => expect(verifyTotp).toHaveBeenCalledWith("123456"));
    // The initial mount refresh only; a successful verify already applied the session.
    expect(refreshSession).toHaveBeenCalledTimes(1);
  });

  it("falls back to a session refresh (and thus the TOTP dialog) when the post-enrollment verify fails", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    const verifyTotp = vi.fn().mockRejectedValue(new Error("invalid code"));
    setAuthState({
      refreshSession,
      verifyTotp,
      passwordRequired: true,
      authenticated: true,
      totpEnrollmentRequired: true,
      user: createSessionUser(),
    });

    renderGate();
    screen.getByRole("button", { name: "Enrollment form" }).click();

    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(2));
  });

  it("offers a retry instead of the router when the session request itself failed", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: false,
      authenticated: false,
      error: "Request failed",
    });

    renderGate();

    expect(screen.getByTestId("route-load-error")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));

    screen.getByTestId("route-retry").click();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(2));
  });
  describe("local login policy", () => {
    const PROXY = { kind: "trusted_header", providerKey: "default", label: "Reverse proxy", loginUrl: null };

    it("hides the password form under break_glass_only and shows it at /login?local=1", () => {
      const hint = LoginHintSchema.parse({
        usernameField: "shown",
        providers: [PROXY],
        localLogin: "break_glass_only",
      });
      setAuthState({ refreshSession: vi.fn().mockResolvedValue(undefined), loginHint: hint });

      const view = renderGate("/");
      expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
      expect(screen.getByTestId("login-providers")).toBeInTheDocument();
      view.unmount();

      setAuthState({ refreshSession: vi.fn().mockResolvedValue(undefined), loginHint: hint });
      renderGate("/login?local=1");
      expect(screen.getByLabelText("Password")).toBeInTheDocument();
    });

    it("collapses the form under admins_only unless the URL asks for it", () => {
      const hint = LoginHintSchema.parse({ usernameField: "shown", localLogin: "admins_only" });
      setAuthState({ refreshSession: vi.fn().mockResolvedValue(undefined), loginHint: hint });

      const view = renderGate("/");
      expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Sign in with a password instead" })).toBeInTheDocument();
      view.unmount();

      setAuthState({ refreshSession: vi.fn().mockResolvedValue(undefined), loginHint: hint });
      renderGate("/login?local=1");
      expect(screen.getByLabelText("Password")).toBeInTheDocument();
    });

    it("offers the emergency link, not a second form, on the pending screen", () => {
      setAuthState({
        refreshSession: vi.fn().mockResolvedValue(undefined),
        authMode: "trusted_header",
        localPasswordConfigured: true,
        loginHint: LoginHintSchema.parse({
          usernameField: "shown",
          providers: [{ kind: "password", providerKey: "default", label: "Password", loginUrl: null }, PROXY],
          localLogin: "break_glass_only",
          pendingIdentity: true,
        }),
      });

      renderGate("/auth/pending");

      expect(screen.getByText("Your account is not ready yet")).toBeInTheDocument();
      expect(screen.queryByTestId("pending-local-login")).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Emergency sign-in with a password" })).toHaveAttribute(
        "href",
        "/login?local=1",
      );
    });
  });
});

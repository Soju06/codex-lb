import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
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
});

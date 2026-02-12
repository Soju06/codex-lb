import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

function setAuthState(
  patch: Partial<ReturnType<typeof useAuthStore.getState>>,
): void {
  useAuthStore.setState({
    initialized: true,
    loading: false,
    passwordRequired: true,
    authenticated: false,
    totpRequiredOnLogin: false,
    error: null,
    ...patch,
  });
}

describe("AuthGate", () => {
  beforeEach(() => {
    setAuthState({
      refreshSession: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("shows login form when unauthenticated", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    setAuthState({
      refreshSession,
      passwordRequired: true,
      authenticated: false,
      totpRequiredOnLogin: false,
    });

    render(
      <AuthGate>
        <div>Protected content</div>
      </AuthGate>,
    );

    expect(screen.getByText("Dashboard Login")).toBeInTheDocument();
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

    render(
      <AuthGate>
        <div>Protected content</div>
      </AuthGate>,
    );

    expect(screen.getByText("Protected content")).toBeInTheDocument();
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

    render(
      <AuthGate>
        <div>Protected content</div>
      </AuthGate>,
    );

    expect(screen.getByText("Two-factor verification")).toBeInTheDocument();
    expect(screen.queryByText("Dashboard Login")).not.toBeInTheDocument();
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
  });
});

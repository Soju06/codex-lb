import { beforeEach, describe, expect, it, vi } from "vitest";

let registeredUnauthorizedHandler: (() => void) | null = null;
const getAuthSession = vi.fn();

vi.mock("@/features/auth/api", () => ({
  getAuthSession,
  loginGuest: vi.fn(),
  loginPassword: vi.fn(),
  logout: vi.fn(),
  verifyTotp: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  setUnauthorizedHandler: (handler: (() => void) | null) => {
    registeredUnauthorizedHandler = handler;
  },
}));

describe("useAuthStore unauthorized handler", () => {
  beforeEach(() => {
    vi.resetModules();
    getAuthSession.mockReset();
    registeredUnauthorizedHandler = null;
  });

  it("refreshes server auth state on 401 handling", async () => {
    const { useAuthStore } = await import("@/features/auth/hooks/use-auth");
    getAuthSession.mockResolvedValue({
      passwordRequired: false,
      authenticated: false,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      bootstrapRequired: true,
      bootstrapTokenConfigured: true,
      authMode: "standard",
      passwordManagementEnabled: true,
      passwordSessionActive: false,
      role: "guest",
      permissions: ["read"],
      guestAccessEnabled: true,
      guestPasswordRequired: true,
    });

    useAuthStore.setState({
      authenticated: true,
      initialized: true,
      bootstrapRequired: false,
      bootstrapTokenConfigured: false,
      guestAccessEnabled: true,
      guestPasswordRequired: false,
      error: "boom",
    });

    expect(registeredUnauthorizedHandler).not.toBeNull();
    registeredUnauthorizedHandler?.();
    await vi.waitFor(() => expect(getAuthSession).toHaveBeenCalledTimes(1));

    const next = useAuthStore.getState();
    expect(next.authenticated).toBe(false);
    expect(next.initialized).toBe(true);
    expect(next.error).toBeNull();
    expect(next.bootstrapRequired).toBe(true);
    expect(next.bootstrapTokenConfigured).toBe(true);
    expect(next.guestPasswordRequired).toBe(true);
  });

  it("clears write permissions for guest deployments on 401 handling", async () => {
    const { useAuthStore } = await import("@/features/auth/hooks/use-auth");

    useAuthStore.setState({
      authenticated: true,
      initialized: true,
      role: "admin",
      permissions: ["read", "write"],
      canWrite: true,
      guestAccessEnabled: true,
      guestPasswordRequired: true,
    });

    expect(registeredUnauthorizedHandler).not.toBeNull();
    registeredUnauthorizedHandler?.();

    const next = useAuthStore.getState();
    expect(next.authenticated).toBe(false);
    expect(next.role).toBe("guest");
    expect(next.permissions).toEqual(["read"]);
    expect(next.canWrite).toBe(false);
  });

  it("keeps admin upgrade login visible after a failed password attempt", async () => {
    const { useAuthStore } = await import("@/features/auth/hooks/use-auth");

    useAuthStore.setState({
      authenticated: true,
      initialized: true,
      role: "guest",
      permissions: ["read"],
      canWrite: false,
      guestAccessEnabled: true,
      guestPasswordRequired: false,
      adminLoginRequested: true,
      error: "Invalid password",
    });

    expect(registeredUnauthorizedHandler).not.toBeNull();
    registeredUnauthorizedHandler?.();

    expect(getAuthSession).not.toHaveBeenCalled();
    const next = useAuthStore.getState();
    expect(next.authenticated).toBe(true);
    expect(next.role).toBe("guest");
    expect(next.adminLoginRequested).toBe(true);
    expect(next.error).toBe("Invalid password");
  });
});

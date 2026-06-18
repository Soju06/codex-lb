import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ApiError } from "@/lib/api-client";

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
  ApiError: class ApiError extends Error {
    status: number;
    code: string;

    constructor({ message, status, code }: { message: string; status: number; code: string }) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
    }
  },
  setUnauthorizedHandler: (handler: (() => void) | null) => {
    registeredUnauthorizedHandler = handler;
  },
}));

describe("useAuthStore unauthorized handler", () => {
  beforeEach(() => {
    getAuthSession.mockReset();
    vi.clearAllMocks();
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
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(getAuthSession).toHaveBeenCalledTimes(1);

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

  it("skips session refresh while admin login request is in progress", async () => {
    const { useAuthStore } = await import("@/features/auth/hooks/use-auth");
    const { loginPassword } = await import("@/features/auth/api");

    let rejectLogin: ((error: Error) => void) | null = null;
    (loginPassword as Mock).mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectLogin = reject;
        }),
    );

    useAuthStore.setState({
      authenticated: true,
      initialized: true,
      role: "guest",
      permissions: ["read"],
      canWrite: false,
      guestAccessEnabled: true,
      guestPasswordRequired: false,
      adminLoginRequested: false,
      error: null,
    });

    const loginPromise = useAuthStore.getState().login("wrong-pass");

    expect(registeredUnauthorizedHandler).not.toBeNull();
    registeredUnauthorizedHandler?.();

    expect(getAuthSession).toHaveBeenCalledTimes(0);
    expect(rejectLogin).not.toBeNull();
    const failLogin = rejectLogin as unknown as (error: Error) => void;

    failLogin(
      new ApiError({
        message: "Invalid credentials",
        status: 401,
        code: "invalid_credentials",
      }),
    );
    await expect(loginPromise).rejects.toBeInstanceOf(ApiError);

    const next = useAuthStore.getState();
    expect(next.adminLoginRequested).toBe(true);
    expect(next.initialized).toBe(true);
    expect(next.loading).toBe(false);
  });
});

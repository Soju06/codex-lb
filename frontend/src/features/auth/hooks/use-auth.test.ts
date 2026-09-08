import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  getAuthSession,
  loginGuest,
  loginPassword,
  logout as logoutRequest,
  verifyTotp as verifyTotpRequest,
} from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { createDashboardAuthSession } from "@/test/mocks/factories";

vi.mock("@/features/auth/api", () => ({
  getAuthSession: vi.fn(),
  loginPassword: vi.fn(),
  loginGuest: vi.fn(),
  logout: vi.fn(),
  verifyTotp: vi.fn(),
}));

const sessionBase = createDashboardAuthSession();

function resetAuthStore(): void {
  useAuthStore.setState({
    passwordRequired: false,
    authenticated: false,
    totpRequiredOnLogin: false,
    totpConfigured: false,
    bootstrapRequired: false,
    bootstrapTokenConfigured: false,
    authMode: "standard",
    passwordManagementEnabled: true,
    passwordSessionActive: false,
    role: "guest",
    permissions: [],
    guestAccessEnabled: false,
    guestPasswordRequired: false,
    canWrite: false,
    adminLoginRequested: false,
    loading: false,
    initialized: false,
    error: null,
  });
}

describe("useAuthStore initial state", () => {
  it("starts with least privilege before the session resolves", () => {
    const initial = useAuthStore.getInitialState();

    expect(initial.initialized).toBe(false);
    expect(initial.authenticated).toBe(false);
    expect(initial.role).toBe("guest");
    expect(initial.permissions).toEqual([]);
    expect(initial.canWrite).toBe(false);
  });
});

describe("useAuthStore actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetAuthStore();
  });

  it("applySession grants write access only when the backend lists it", async () => {
    (getAuthSession as Mock).mockResolvedValue({
      ...sessionBase,
      role: "admin",
      permissions: ["read", "write", "accounts:export"],
    });

    await useAuthStore.getState().refreshSession();

    const next = useAuthStore.getState();
    expect(next.role).toBe("admin");
    expect(next.permissions).toEqual(["read", "write", "accounts:export"]);
    expect(next.canWrite).toBe(true);
  });

  it("logout resets to least privilege instead of admin defaults before refreshing", async () => {
    useAuthStore.setState({
      authenticated: true,
      passwordRequired: true,
      initialized: true,
      role: "admin",
      permissions: ["read", "write"],
      canWrite: true,
    });

    let stateDuringRefresh: ReturnType<typeof useAuthStore.getState> | null = null;
    (logoutRequest as Mock).mockResolvedValue({ status: "ok" });
    (getAuthSession as Mock).mockImplementation(async () => {
      stateDuringRefresh = useAuthStore.getState();
      return { ...sessionBase, authenticated: false };
    });

    await useAuthStore.getState().logout();

    expect(stateDuringRefresh).not.toBeNull();
    expect(stateDuringRefresh!.role).toBe("guest");
    expect(stateDuringRefresh!.permissions).toEqual([]);
    expect(stateDuringRefresh!.canWrite).toBe(false);
    expect(stateDuringRefresh!.authenticated).toBe(false);
  });

  it("refreshSession updates auth state", async () => {
    (getAuthSession as Mock).mockResolvedValue({
      ...sessionBase,
      authenticated: false,
      totpRequiredOnLogin: true,
    });

    await useAuthStore.getState().refreshSession();

    const next = useAuthStore.getState();
    expect(next.initialized).toBe(true);
    expect(next.authenticated).toBe(false);
    expect(next.totpRequiredOnLogin).toBe(true);
    expect(next.loading).toBe(false);
  });

  it("login updates session state", async () => {
    (loginPassword as Mock).mockResolvedValue(sessionBase);

    await useAuthStore.getState().login("secret-pass");

    const next = useAuthStore.getState();
    expect(loginPassword).toHaveBeenCalledWith({ password: "secret-pass" });
    expect(next.authenticated).toBe(true);
    expect(next.error).toBeNull();
  });

  it("guest login stores read-only permissions", async () => {
    (loginGuest as Mock).mockResolvedValue({
      ...sessionBase,
      role: "guest",
      permissions: ["read"],
      guestAccessEnabled: true,
    });

    await useAuthStore.getState().loginGuest("guest-pass");

    const next = useAuthStore.getState();
    expect(loginGuest).toHaveBeenCalledWith({ password: "guest-pass" });
    expect(next.role).toBe("guest");
    expect(next.canWrite).toBe(false);
  });

  it("logout clears auth and refreshes session", async () => {
    useAuthStore.setState({
      authenticated: true,
      passwordRequired: true,
      initialized: true,
    });

    (logoutRequest as Mock).mockResolvedValue({ status: "ok" });
    (getAuthSession as Mock).mockResolvedValue({
      ...sessionBase,
      authenticated: false,
      totpRequiredOnLogin: false,
    });

    await useAuthStore.getState().logout();

    const next = useAuthStore.getState();
    expect(logoutRequest).toHaveBeenCalledTimes(1);
    expect(getAuthSession).toHaveBeenCalledTimes(1);
    expect(next.authenticated).toBe(false);
    expect(next.loading).toBe(false);
  });

  it("verifyTotp updates state transitions", async () => {
    (verifyTotpRequest as Mock).mockResolvedValue({
      ...sessionBase,
      authenticated: true,
      totpRequiredOnLogin: false,
    });

    await useAuthStore.getState().verifyTotp("123456");

    const next = useAuthStore.getState();
    expect(verifyTotpRequest).toHaveBeenCalledWith({ code: "123456" });
    expect(next.authenticated).toBe(true);
    expect(next.totpRequiredOnLogin).toBe(false);
    expect(next.loading).toBe(false);
  });
});

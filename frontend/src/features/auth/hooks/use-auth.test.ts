import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  getAuthSession,
  loginPassword,
  logout as logoutRequest,
  verifyTotp as verifyTotpRequest,
} from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import type { AuthSession } from "@/features/auth/schemas";

vi.mock("@/features/auth/api", () => ({
  getAuthSession: vi.fn(),
  loginPassword: vi.fn(),
  logout: vi.fn(),
  verifyTotp: vi.fn(),
}));

const sessionBase: AuthSession = {
  authenticated: true,
  passwordRequired: true,
  totpRequiredOnLogin: false,
  totpConfigured: true,
  bootstrapRequired: false,
  bootstrapTokenConfigured: false,
  authMode: "standard",
  passwordManagementEnabled: true,
};

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
    loading: false,
    initialized: false,
    error: null,
  });
}

describe("useAuthStore actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetAuthStore();
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

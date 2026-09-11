import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  acceptInvite as acceptInviteRequest,
  getAuthSession,
  loginGuest,
  loginPassword,
  logout as logoutRequest,
  logoutAll as logoutAllRequest,
  verifyTotp as verifyTotpRequest,
} from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { LAST_USERNAME_STORAGE_KEY } from "@/features/auth/last-username";
import { LoginHintSchema } from "@/features/auth/schemas";
import {
  ADMIN_PERMISSIONS,
  createAccessSummary,
  createDashboardAuthSession,
  createSessionUser,
} from "@/test/mocks/factories";

vi.mock("@/features/auth/api", () => ({
  acceptInvite: vi.fn(),
  getAuthSession: vi.fn(),
  loginPassword: vi.fn(),
  loginGuest: vi.fn(),
  logout: vi.fn(),
  logoutAll: vi.fn(),
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
    user: null,
    accessSummary: null,
    tier: "individual",
    totpEnrollmentRequired: false,
    mustChangePassword: false,
    loginHint: LoginHintSchema.parse({}),
    adminLoginRequested: false,
    loading: false,
    initialized: false,
    error: null,
  });
  window.localStorage.removeItem(LAST_USERNAME_STORAGE_KEY);
}

describe("useAuthStore initial state", () => {
  it("starts with least privilege before the session resolves", () => {
    const initial = useAuthStore.getInitialState();

    expect(initial.initialized).toBe(false);
    expect(initial.authenticated).toBe(false);
    expect(initial.role).toBe("guest");
    expect(initial.permissions).toEqual([]);
    expect(initial.canWrite).toBe(false);
    expect(initial.user).toBeNull();
    expect(initial.tier).toBe("individual");
    expect(initial.loginHint.usernameField).toBe("hidden");
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

  it("sends the username when given and remembers the account that signed in", async () => {
    (loginPassword as Mock).mockResolvedValue({ ...sessionBase, user: createSessionUser({ username: "alice" }) });

    await useAuthStore.getState().login("secret-pass", "alice");

    expect(loginPassword).toHaveBeenCalledWith({ username: "alice", password: "secret-pass" });
    expect(window.localStorage.getItem(LAST_USERNAME_STORAGE_KEY)).toBe("alice");
    expect(useAuthStore.getState().user?.username).toBe("alice");
  });

  it("forgets the remembered username after a username-less sign-in on a single-account install", async () => {
    window.localStorage.setItem(LAST_USERNAME_STORAGE_KEY, "alice");
    (loginPassword as Mock).mockResolvedValue({ ...sessionBase, user: null });

    await useAuthStore.getState().login("secret-pass");

    expect(window.localStorage.getItem(LAST_USERNAME_STORAGE_KEY)).toBeNull();
  });

  it("puts a signed-in account without team facts (no users:manage) on the team tier", async () => {
    (getAuthSession as Mock).mockResolvedValue({
      ...sessionBase,
      permissions: ["read", "dashboard:read:all", "accounts:read:all"],
      user: createSessionUser({ username: "viewer" }),
      accessSummary: null,
    });

    await useAuthStore.getState().refreshSession();

    expect(useAuthStore.getState().tier).toBe("team");
  });

  it("acceptInvite applies the returned session like login does", async () => {
    (acceptInviteRequest as Mock).mockResolvedValue({
      ...sessionBase,
      user: createSessionUser({ username: "sarah" }),
      accessSummary: null,
    });

    await useAuthStore.getState().acceptInvite({ token: "tok", username: "sarah", password: "strong-password" });

    expect(acceptInviteRequest).toHaveBeenCalledWith({ token: "tok", username: "sarah", password: "strong-password" });
    const next = useAuthStore.getState();
    expect(next.authenticated).toBe(true);
    expect(next.user?.username).toBe("sarah");
    expect(next.tier).toBe("team");
    expect(next.loading).toBe(false);
  });

  it("derives the disclosure tier and the account block from the session", async () => {
    (getAuthSession as Mock).mockResolvedValue({
      ...sessionBase,
      permissions: ADMIN_PERMISSIONS,
      user: createSessionUser(),
      accessSummary: createAccessSummary({ usersTotal: 2, nonAdminUsers: 1 }),
      login: { usernameField: "shown", providers: [], localLogin: "enabled" },
    });

    await useAuthStore.getState().refreshSession();

    const next = useAuthStore.getState();
    expect(next.tier).toBe("team");
    expect(next.user?.username).toBe("admin");
    expect(next.loginHint.usernameField).toBe("shown");
  });

  it("answers can() and scope() from the scoped permission strings only", async () => {
    (getAuthSession as Mock).mockResolvedValue({
      ...sessionBase,
      permissions: ["read", "dashboard:read:own", "api_keys:read:own", "accounts:read:all"],
    });

    await useAuthStore.getState().refreshSession();

    const { can, scope } = useAuthStore.getState();
    expect(can("dashboard:read")).toBe(true);
    expect(scope("dashboard:read")).toBe("own");
    expect(scope("accounts:read")).toBe("all");
    expect(can("users:manage")).toBe(false);
    expect(scope("users:manage")).toBeNull();
  });

  it("logoutEverywhere revokes every session and resets to least privilege before refreshing", async () => {
    useAuthStore.setState({
      authenticated: true,
      initialized: true,
      role: "admin",
      permissions: ADMIN_PERMISSIONS,
      canWrite: true,
      user: createSessionUser(),
      tier: "team",
    });
    (logoutAllRequest as Mock).mockResolvedValue({ status: "ok" });
    let stateDuringRefresh: ReturnType<typeof useAuthStore.getState> | null = null;
    (getAuthSession as Mock).mockImplementation(async () => {
      stateDuringRefresh = useAuthStore.getState();
      return { ...sessionBase, authenticated: false };
    });

    await useAuthStore.getState().logoutEverywhere();

    expect(logoutAllRequest).toHaveBeenCalledTimes(1);
    expect(logoutRequest).not.toHaveBeenCalled();
    expect(stateDuringRefresh!.user).toBeNull();
    expect(stateDuringRefresh!.tier).toBe("individual");
    expect(stateDuringRefresh!.permissions).toEqual([]);
  });
});

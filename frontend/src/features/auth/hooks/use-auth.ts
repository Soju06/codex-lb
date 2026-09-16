import { ApiError, setUnauthorizedHandler } from "@/lib/api-client";
import { create } from "zustand";

import {
  acceptInvite as acceptInviteRequest,
  getAuthSession,
  loginGuest as loginGuestRequest,
  loginPassword,
  logout as logoutRequest,
  logoutAll as logoutAllRequest,
  verifyTotp as verifyTotpRequest,
} from "@/features/auth/api";
import { resolveDisclosureTier, type DisclosureTier } from "@/features/auth/disclosure";
import { rememberLastUsername } from "@/features/auth/last-username";
import {
  LoginHintSchema,
  type AccessSummary,
  type AuthSession,
  type AuthSessionUser,
  type DashboardAuthMode,
  type DashboardRole,
  type InviteAcceptRequest,
  type LoginHint,
  type Permission,
  type PermissionScope,
  type StepUpState,
} from "@/features/auth/schemas";

let isAdminLoginInProgress = false;

type AuthState = {
  passwordRequired: boolean;
  /** An active account holds a password; drives the Password card, unlike `passwordRequired`. */
  localPasswordConfigured: boolean;
  authenticated: boolean;
  totpRequiredOnLogin: boolean;
  totpConfigured: boolean;
  bootstrapRequired: boolean;
  bootstrapTokenConfigured: boolean;
  authMode: DashboardAuthMode;
  passwordManagementEnabled: boolean;
  passwordSessionActive: boolean;
  role: DashboardRole;
  permissions: string[];
  guestAccessEnabled: boolean;
  guestPasswordRequired: boolean;
  canWrite: boolean;
  user: AuthSessionUser | null;
  mustChangePassword: boolean;
  totpEnrollmentRequired: boolean;
  loginHint: LoginHint;
  accessSummary: AccessSummary | null;
  /** Role ids this account may hand out (`assignable_role_ids`); empty without `users:manage`. */
  assignableRoleIds: string[];
  /** Recent re-verification and the factors the account can re-verify with; `null` without an account. */
  stepUp: StepUpState | null;
  /** This session belongs to a break-glass account; the header shows the emergency indicator. */
  breakGlassSession: boolean;
  tier: DisclosureTier;
  adminLoginRequested: boolean;
  loading: boolean;
  initialized: boolean;
  error: string | null;
  refreshSession: () => Promise<AuthSession>;
  login: (password: string, username?: string) => Promise<AuthSession>;
  loginGuest: (password?: string) => Promise<AuthSession>;
  /** Public invite acceptance; the response is the new account's session. */
  acceptInvite: (payload: InviteAcceptRequest) => Promise<AuthSession>;
  startAdminLogin: () => void;
  logout: () => Promise<void>;
  logoutEverywhere: () => Promise<void>;
  verifyTotp: (code: string) => Promise<AuthSession>;
  clearError: () => void;
  /** True when the session grants `permission` at any scope (`all` or `own`). */
  can: (permission: Permission) => boolean;
  /** The granted scope for `permission`, or `null` when it is not granted. */
  scope: (permission: Permission) => PermissionScope | null;
};

const DEFAULT_LOGIN_HINT: LoginHint = LoginHintSchema.parse({});

// The store starts (and is reset on logout) with the least privilege the
// backend can grant, so nothing renders admin controls before the session
// response says so.
const LEAST_PRIVILEGE_ACCESS: Pick<
  AuthState,
  | "role"
  | "permissions"
  | "canWrite"
  | "user"
  | "accessSummary"
  | "assignableRoleIds"
  | "stepUp"
  | "breakGlassSession"
  | "tier"
  | "mustChangePassword"
  | "totpEnrollmentRequired"
> = {
  role: "guest",
  permissions: [],
  canWrite: false,
  user: null,
  accessSummary: null,
  assignableRoleIds: [],
  stepUp: null,
  breakGlassSession: false,
  tier: "individual",
  mustChangePassword: false,
  totpEnrollmentRequired: false,
};

export function grantedScope(permissions: readonly string[], permission: Permission): PermissionScope | null {
  if (permissions.includes(`${permission}:all`)) {
    return "all";
  }
  if (permissions.includes(`${permission}:own`)) {
    return "own";
  }
  return null;
}

export function hasPermission(permissions: readonly string[], permission: Permission): boolean {
  return grantedScope(permissions, permission) !== null;
}

/** Reactive form of `can(permission)`: re-renders when the session's grants change. */
export function usePermission(permission: Permission): boolean {
  return useAuthStore((state) => hasPermission(state.permissions, permission));
}

function applySession(set: (next: Partial<AuthState>) => void, session: AuthSession): AuthSession {
  set({
    passwordRequired: session.passwordRequired,
    localPasswordConfigured: session.localPasswordConfigured,
    authenticated: session.authenticated,
    totpRequiredOnLogin: session.totpRequiredOnLogin,
    totpConfigured: session.totpConfigured,
    bootstrapRequired: session.bootstrapRequired ?? false,
    bootstrapTokenConfigured: session.bootstrapTokenConfigured ?? false,
    authMode: session.authMode,
    passwordManagementEnabled: session.passwordManagementEnabled,
    passwordSessionActive: session.passwordSessionActive,
    role: session.role,
    permissions: session.permissions,
    guestAccessEnabled: session.guestAccessEnabled,
    guestPasswordRequired: session.guestPasswordRequired,
    canWrite: session.permissions.includes("write"),
    user: session.user ?? null,
    mustChangePassword: session.mustChangePassword ?? false,
    totpEnrollmentRequired: session.totpEnrollmentRequired ?? false,
    loginHint: session.login ?? DEFAULT_LOGIN_HINT,
    accessSummary: session.accessSummary ?? null,
    assignableRoleIds: session.assignableRoleIds,
    stepUp: session.stepUp ?? null,
    breakGlassSession: session.breakGlassSession ?? false,
    tier: resolveDisclosureTier(session.accessSummary ?? null, session.user ?? null),
    adminLoginRequested: false,
    initialized: true,
    error: null,
  });
  return session;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  passwordRequired: false,
  localPasswordConfigured: false,
  authenticated: false,
  totpRequiredOnLogin: false,
  totpConfigured: false,
  bootstrapRequired: false,
  bootstrapTokenConfigured: false,
  authMode: "standard",
  passwordManagementEnabled: true,
  passwordSessionActive: false,
  ...LEAST_PRIVILEGE_ACCESS,
  guestAccessEnabled: false,
  guestPasswordRequired: false,
  loginHint: DEFAULT_LOGIN_HINT,
  adminLoginRequested: false,
  loading: false,
  initialized: false,
  error: null,
  refreshSession: async () => {
    set({ loading: true, error: null });
    try {
      const session = await getAuthSession();
      return applySession(set, session);
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "Failed to refresh session",
      });
      throw error;
    } finally {
      set({ loading: false, initialized: true });
    }
  },
  login: async (password, username) => {
    set({ loading: true, error: null });
    isAdminLoginInProgress = true;
    try {
      const session = await loginPassword(username ? { username, password } : { password });
      // Remember what was typed, not the resolved account: a single-account
      // install signs in without a username and must keep its password-only
      // form. An install that has come back to one account answers `hidden`,
      // and the remembered name is dropped then even if one was typed -- the
      // form reveals itself while a name is remembered, so keeping it would
      // pin a username box on an install that no longer needs one (P5).
      const hint = session.login ?? DEFAULT_LOGIN_HINT;
      rememberLastUsername(hint.usernameField === "hidden" ? undefined : username);
      return applySession(set, session);
    } catch (error) {
      const shouldKeepAdminLogin =
        useAuthStore.getState().adminLoginRequested ||
        (error instanceof ApiError && error.status === 401);
      set({
        error: error instanceof Error ? error.message : "Login failed",
        adminLoginRequested: shouldKeepAdminLogin,
      });
      throw error;
    } finally {
      isAdminLoginInProgress = false;
      set({ loading: false, initialized: true });
    }
  },
  loginGuest: async (password) => {
    set({ loading: true, error: null });
    try {
      const session = await loginGuestRequest(password ? { password } : {});
      return applySession(set, session);
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "Guest login failed",
      });
      throw error;
    } finally {
      set({ loading: false, initialized: true });
    }
  },
  acceptInvite: async (payload) => {
    set({ loading: true, error: null });
    try {
      const session = await acceptInviteRequest(payload);
      return applySession(set, session);
    } finally {
      set({ loading: false, initialized: true });
    }
  },
  startAdminLogin: () => {
    set({ adminLoginRequested: true, error: null });
  },
  logout: async () => {
    await signOut(set, logoutRequest);
  },
  logoutEverywhere: async () => {
    await signOut(set, logoutAllRequest);
  },
  verifyTotp: async (code) => {
    set({ loading: true, error: null });
    try {
      const session = await verifyTotpRequest({ code });
      return applySession(set, session);
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "TOTP verification failed",
      });
      throw error;
    } finally {
      set({ loading: false, initialized: true });
    }
  },
  clearError: () => {
    set({ error: null });
  },
  can: (permission) => hasPermission(get().permissions, permission),
  scope: (permission) => grantedScope(get().permissions, permission),
}));

async function signOut(set: (next: Partial<AuthState>) => void, request: () => Promise<unknown>): Promise<void> {
  set({ loading: true, error: null });
  try {
    await request();
    set({
      authenticated: false,
      totpRequiredOnLogin: false,
      bootstrapRequired: false,
      bootstrapTokenConfigured: false,
      authMode: "standard",
      passwordManagementEnabled: true,
      ...LEAST_PRIVILEGE_ACCESS,
      adminLoginRequested: false,
    });
    await useAuthStore.getState().refreshSession();
  } finally {
    set({ loading: false });
  }
}

setUnauthorizedHandler(() => {
  if (isAdminLoginInProgress || useAuthStore.getState().adminLoginRequested) {
    useAuthStore.setState({ initialized: true });
    return;
  }

  // Least privilege until the follow-up refresh settles; `initialized: false`
  // keeps the gate on its spinner instead of flashing a frame nobody may see.
  useAuthStore.setState((state) => ({
    ...state,
    authenticated: false,
    ...LEAST_PRIVILEGE_ACCESS,
    role: state.guestAccessEnabled ? "guest" : state.role,
    adminLoginRequested: false,
    initialized: false,
    error: null,
  }));
  void useAuthStore.getState().refreshSession().catch(() => undefined);
});

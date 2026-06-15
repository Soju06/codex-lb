import { ApiError, setUnauthorizedHandler } from "@/lib/api-client";
import { create } from "zustand";

import {
  getAuthSession,
  loginGuest as loginGuestRequest,
  loginPassword,
  logout as logoutRequest,
  verifyTotp as verifyTotpRequest,
} from "@/features/auth/api";
import type {
  AuthSession,
  DashboardAuthMode,
  DashboardPermission,
  DashboardRole,
} from "@/features/auth/schemas";

let isAdminLoginInProgress = false;

type AuthState = {
  passwordRequired: boolean;
  authenticated: boolean;
  totpRequiredOnLogin: boolean;
  totpConfigured: boolean;
  bootstrapRequired: boolean;
  bootstrapTokenConfigured: boolean;
  authMode: DashboardAuthMode;
  passwordManagementEnabled: boolean;
  passwordSessionActive: boolean;
  role: DashboardRole;
  permissions: DashboardPermission[];
  guestAccessEnabled: boolean;
  guestPasswordRequired: boolean;
  canWrite: boolean;
  adminLoginRequested: boolean;
  loading: boolean;
  initialized: boolean;
  error: string | null;
  refreshSession: () => Promise<AuthSession>;
  login: (password: string) => Promise<AuthSession>;
  loginGuest: (password?: string) => Promise<AuthSession>;
  startAdminLogin: () => void;
  logout: () => Promise<void>;
  verifyTotp: (code: string) => Promise<AuthSession>;
  clearError: () => void;
};

function applySession(set: (next: Partial<AuthState>) => void, session: AuthSession): AuthSession {
  set({
    passwordRequired: session.passwordRequired,
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
    adminLoginRequested: false,
    initialized: true,
    error: null,
  });
  return session;
}

export const useAuthStore = create<AuthState>((set) => ({
  passwordRequired: false,
  authenticated: false,
  totpRequiredOnLogin: false,
  totpConfigured: false,
  bootstrapRequired: false,
  bootstrapTokenConfigured: false,
  authMode: "standard",
  passwordManagementEnabled: true,
  passwordSessionActive: false,
  role: "admin",
  permissions: ["read", "write"],
  guestAccessEnabled: false,
  guestPasswordRequired: false,
  canWrite: true,
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
  login: async (password) => {
    set({ loading: true, error: null });
    isAdminLoginInProgress = true;
    try {
      const session = await loginPassword({ password });
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
  startAdminLogin: () => {
    set({ adminLoginRequested: true, error: null });
  },
  logout: async () => {
    set({ loading: true, error: null });
    try {
      await logoutRequest();
      set({
        authenticated: false,
        totpRequiredOnLogin: false,
        bootstrapRequired: false,
        bootstrapTokenConfigured: false,
        authMode: "standard",
        passwordManagementEnabled: true,
        role: "admin",
        permissions: ["read", "write"],
        canWrite: true,
        adminLoginRequested: false,
      });
      await useAuthStore.getState().refreshSession();
    } finally {
      set({ loading: false });
    }
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
}));

setUnauthorizedHandler(() => {
  if (isAdminLoginInProgress || useAuthStore.getState().adminLoginRequested) {
    useAuthStore.setState({ initialized: true });
    return;
  }

  useAuthStore.setState((state) => ({
    ...state,
    authenticated: false,
    role: state.guestAccessEnabled ? "guest" : state.role,
    permissions: ["read"],
    canWrite: false,
    adminLoginRequested: false,
    initialized: true,
    error: null,
  }));
  void useAuthStore.getState().refreshSession().catch(() => undefined);
});

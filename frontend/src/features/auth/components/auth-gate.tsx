import { useEffect } from "react";
import type { PropsWithChildren } from "react";
import { useTranslation } from "react-i18next";
import { matchPath, useLocation, useNavigate } from "react-router-dom";

import { RouteLoadError } from "@/components/layout/route-recovery";
import { Button } from "@/components/ui/button";
import { SpinnerBlock } from "@/components/ui/spinner";
import { AuthScreenFrame } from "@/features/auth/components/auth-screen-frame";
import { BootstrapSetupScreen } from "@/features/auth/components/bootstrap-setup-screen";
import { InviteAcceptScreen } from "@/features/auth/components/invite-accept-screen";
import { LoginForm } from "@/features/auth/components/login-form";
import { PendingIdentityScreen } from "@/features/auth/components/pending-identity-screen";
import { TotpDialog } from "@/features/auth/components/totp-dialog";
import { TotpEnrollmentForm } from "@/features/auth/components/totp-enrollment-form";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { isLocalLoginRequested, localFormDisclosure, LOCAL_LOGIN_ROUTE } from "@/features/auth/local-login";

// Public routes render before the login branch: they must work for a visitor
// without a session and for a signed-in account alike.
export const INVITE_ROUTE_PATTERN = "/invite/:token";
export const PENDING_IDENTITY_ROUTE = "/auth/pending";
const LOCAL_LOGIN_ROUTE_PREFIX = LOCAL_LOGIN_ROUTE;

function isPublicRoute(pathname: string): boolean {
  return (
    pathname === PENDING_IDENTITY_ROUTE ||
    matchPath(INVITE_ROUTE_PATTERN, pathname) !== null ||
    pathname === LOCAL_LOGIN_ROUTE_PREFIX ||
    pathname.startsWith(`${LOCAL_LOGIN_ROUTE_PREFIX}/`)
  );
}

export function AuthGate({ children }: PropsWithChildren) {
  const { t } = useTranslation();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const refreshSessionStable = useAuthStore((state) => state.refreshSession);
  const initialized = useAuthStore((state) => state.initialized);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);
  const authenticated = useAuthStore((state) => state.authenticated);
  const bootstrapRequired = useAuthStore((state) => state.bootstrapRequired);
  const totpRequiredOnLogin = useAuthStore((state) => state.totpRequiredOnLogin);
  const totpEnrollmentRequired = useAuthStore((state) => state.totpEnrollmentRequired);
  const authMode = useAuthStore((state) => state.authMode);
  const adminLoginRequested = useAuthStore((state) => state.adminLoginRequested);
  const guestAccessEnabled = useAuthStore((state) => state.guestAccessEnabled);
  const guestPasswordRequired = useAuthStore((state) => state.guestPasswordRequired);
  const logout = useAuthStore((state) => state.logout);
  const verifyTotp = useAuthStore((state) => state.verifyTotp);
  const error = useAuthStore((state) => state.error);
  const pendingIdentity = useAuthStore((state) => state.loginHint.pendingIdentity);
  const localLoginPolicy = useAuthStore((state) => state.loginHint.localLogin);
  // One decision, applied by every screen that can draw the local form.
  const localForm = localFormDisclosure(localLoginPolicy, { localRequested: isLocalLoginRequested(search) });

  useEffect(() => {
    void refreshSessionStable();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The proxy vouched for someone without an account: park them on the public
  // pending route so a retry (or a later refresh) lands on the same screen.
  // Other public routes (invite links, the local login) keep their URL.
  const onPublicRoute = isPublicRoute(pathname);
  useEffect(() => {
    if (initialized && pendingIdentity && !authenticated && !onPublicRoute) {
      navigate(PENDING_IDENTITY_ROUTE, { replace: true });
    }
  }, [authenticated, initialized, navigate, onPublicRoute, pendingIdentity]);

  // Hold the whole app until the session resolves: the store starts
  // least-privilege, so rendering early would flash a read-only frame for
  // admins, and rendering with stale state could expose admin controls.
  if (!initialized) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <SpinnerBlock />
      </div>
    );
  }

  const inviteMatch = matchPath(INVITE_ROUTE_PATTERN, pathname);
  if (inviteMatch?.params.token) {
    return <InviteAcceptScreen key={inviteMatch.params.token} token={inviteMatch.params.token} />;
  }

  if (
    !authenticated &&
    (pendingIdentity || (pathname === PENDING_IDENTITY_ROUTE && authMode === "trusted_header"))
  ) {
    return <PendingIdentityScreen localForm={localForm} />;
  }

  if (bootstrapRequired && !passwordRequired) {
    return <BootstrapSetupScreen />;
  }

  if (
    (passwordRequired || (authMode === "standard" && guestAccessEnabled && guestPasswordRequired)) &&
    (!authenticated || adminLoginRequested)
  ) {
    if (totpRequiredOnLogin) {
      return <TotpDialog open />;
    }
    return (
      <AuthScreenFrame>
        <LoginForm localForm={localForm} />
      </AuthScreenFrame>
    );
  }

  if (authMode === "trusted_header" && !authenticated) {
    return (
      <div className="relative flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-lg rounded-2xl border bg-card p-6 shadow-sm">
          <h1 className="text-lg font-semibold tracking-tight">{t("auth.trustedHeader.title")}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {t("auth.trustedHeader.body")}
          </p>
        </div>
      </div>
    );
  }

  // The account signed in but the install requires an authenticator it has not
  // enrolled yet: every other API answers 403 until `/totp/setup` completes.
  // Confirming does not verify the session, so the confirmed code is verified
  // right away; if that fails the refreshed session falls back to the TOTP dialog.
  const handleEnrolled = async (code: string) => {
    try {
      await verifyTotp(code);
    } catch {
      await refreshSessionStable();
    }
  };
  if (authenticated && totpEnrollmentRequired) {
    return (
      <AuthScreenFrame title={t("auth.enroll.title")} subtitle={t("auth.enroll.subtitle")}>
        <div className="rounded-2xl border bg-card p-6 shadow-[var(--shadow-md)]">
          <p className="mb-4 text-sm text-muted-foreground">{t("auth.enroll.description")}</p>
          <TotpEnrollmentForm onEnrolled={handleEnrolled} />
          <Button type="button" variant="ghost" className="mt-4 w-full text-xs" onClick={() => void logout()}>
            {t("common.logout")}
          </Button>
        </div>
      </AuthScreenFrame>
    );
  }

  // The session request itself failed (proxy 5xx, network): nothing below can
  // decide anything, so offer a retry instead of falling through to the router.
  if (error !== null && !authenticated) {
    return (
      <div className="flex min-h-screen flex-col">
        <RouteLoadError onRetry={() => void refreshSessionStable().catch(() => undefined)} />
      </div>
    );
  }

  return <>{children}</>;
}

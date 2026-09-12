import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { AuthScreenFrame } from "@/features/auth/components/auth-screen-frame";
import { LoginForm } from "@/features/auth/components/login-form";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { LOCAL_LOGIN_URL, type LocalFormDisclosure } from "@/features/auth/local-login";

/**
 * Shown when the reverse proxy vouched for a person the dashboard has no
 * account for (or whose account is disabled). Nothing to fill in: an
 * administrator adds them, then a retry picks the account up. When a local
 * password account exists, the break-glass login stays reachable underneath —
 * subject to the same login policy as the login screen, so this screen can
 * never become a second door into a form the policy closed.
 */
export function PendingIdentityScreen({ localForm = "shown" }: { localForm?: LocalFormDisclosure }) {
  const { t } = useTranslation();
  const refreshSession = useAuthStore((state) => state.refreshSession);
  const logout = useAuthStore((state) => state.logout);
  const localLoginAvailable = useAuthStore(
    (state) =>
      state.localPasswordConfigured && state.loginHint.providers.some((provider) => provider.kind === "password"),
  );
  return (
    <AuthScreenFrame title={t("auth.pending.title")} subtitle={t("auth.pending.subtitle")}>
      <div className="rounded-2xl border bg-card p-6 shadow-[var(--shadow-md)]" data-testid="pending-identity">
        <p className="text-sm text-muted-foreground">{t("auth.pending.body")}</p>
        <div className="mt-4 flex gap-2">
          <Button type="button" className="flex-1" onClick={() => void refreshSession().catch(() => undefined)}>
            {t("auth.pending.retry")}
          </Button>
          <Button type="button" variant="ghost" onClick={() => void logout().catch(() => undefined)}>
            {t("common.logout")}
          </Button>
        </div>
      </div>
      {localLoginAvailable && localForm !== "hidden" ? (
        <div className="mt-6 space-y-3" data-testid="pending-local-login">
          <p className="text-center text-xs text-muted-foreground">{t("auth.pending.localLogin")}</p>
          <LoginForm localForm={localForm} />
        </div>
      ) : null}
      {localLoginAvailable && localForm === "hidden" ? (
        <p className="mt-6 text-center text-xs text-muted-foreground">
          <Link to={LOCAL_LOGIN_URL} className="underline underline-offset-4 hover:text-foreground">
            {t("auth.pending.emergencyLink")}
          </Link>
        </p>
      ) : null}
    </AuthScreenFrame>
  );
}

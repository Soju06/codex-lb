import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { AuthScreenFrame } from "@/features/auth/components/auth-screen-frame";
import { LoginForm } from "@/features/auth/components/login-form";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { LOCAL_LOGIN_URL, type LocalFormDisclosure } from "@/features/auth/local-login";

/**
 * Shown when a sign-in method vouched for a person the dashboard has no
 * account for (or whose account is disabled). Nothing to fill in: an
 * administrator adds them, then a retry picks the account up. When a local
 * password account exists, the break-glass login stays reachable underneath —
 * subject to the same login policy as the login screen, so this screen can
 * never become a second door into a form the policy closed.
 *
 * A company sign-in refusal additionally carries `pendingArrival`: the
 * provider's own label and the reference the server computed from the address
 * the identity provider asserted. Both are rendered verbatim — the masking rule
 * belongs to the server, and the refused sign-ins list applies the same one to
 * the address it shows an administrator, which is what lets the two be matched
 * by eye. Without that block (a reverse-proxy arrival, a marker that has
 * expired, or an identity provider that asserted no address) the screen keeps
 * its general copy rather than guessing which provider refused somebody.
 */
export function PendingIdentityScreen({ localForm = "shown" }: { localForm?: LocalFormDisclosure }) {
  const { t } = useTranslation();
  const refreshSession = useAuthStore((state) => state.refreshSession);
  const logout = useAuthStore((state) => state.logout);
  const arrival = useAuthStore((state) => state.loginHint.pendingArrival);
  const localLoginAvailable = useAuthStore(
    (state) =>
      state.localPasswordConfigured && state.loginHint.providers.some((provider) => provider.kind === "password"),
  );
  return (
    <AuthScreenFrame
      title={t("auth.pending.title")}
      subtitle={
        arrival ? t("auth.pending.subtitleProvider", { provider: arrival.provider }) : t("auth.pending.subtitle")
      }
    >
      <div className="rounded-2xl border bg-card p-6 shadow-[var(--shadow-md)]" data-testid="pending-identity">
        <p className="text-sm text-muted-foreground">
          {arrival ? t("auth.pending.bodyProvider", { provider: arrival.provider }) : t("auth.pending.body")}
        </p>
        {arrival ? (
          <div className="mt-4 rounded-lg border bg-muted/40 px-3 py-2">
            <p className="text-xs text-muted-foreground">{t("auth.pending.referenceLabel")}</p>
            <p className="mt-0.5 font-mono text-sm break-all select-all" data-testid="pending-reference">
              {arrival.reference}
            </p>
          </div>
        ) : null}
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

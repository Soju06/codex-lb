import { KeyRound } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SpinnerBlock } from "@/components/ui/spinner";
import { useDashboardUsers } from "@/features/access/hooks";
import { LOCAL_LOGIN_URL } from "@/features/auth/local-login";
import type { LocalLoginPolicy } from "@/features/auth/schemas";
import {
  breakGlassAccountFromError,
  organisationErrorMessage,
  useOrganisationMutations,
  useOrganisationSettings,
} from "@/features/organisation/hooks";
import { breakGlassDesignations, isQualifyingBreakGlass } from "@/features/organisation/rules";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";

const POLICIES: LocalLoginPolicy[] = ["enabled", "admins_only", "break_glass_only"];

export type LoginPolicyCardProps = {
  /** The whole query, not its data: a failed settings request and a pending one
   * are different things to say, and `undefined` alone cannot tell them apart. */
  settingsQuery: ReturnType<typeof useOrganisationSettings>;
  /** Same, for the people list — an unreadable list must never read as "nobody is designated". */
  usersQuery: ReturnType<typeof useDashboardUsers>;
  /** False without `users:manage`: the policy is still editable, the account name is not ours to show. */
  canSeeAccounts: boolean;
  mutations: ReturnType<typeof useOrganisationMutations>;
  disabled?: boolean;
};

/**
 * Who may still sign in with a local password, and the way back in when the
 * company login is down. The card exists on every install, reverse proxy or
 * not: closing this door is what makes the emergency account matter, and an
 * operator has to be able to read the emergency URL and account name — and
 * copy both into a password manager — *before* closing it.
 *
 * The qualifying state is computed from the same four facts the server uses
 * (PLAN §4.2), so the card can explain the refusal instead of provoking it.
 */
export function LoginPolicyCard({
  settingsQuery,
  usersQuery,
  canSeeAccounts,
  mutations,
  disabled = false,
}: LoginPolicyCardProps) {
  const { t } = useTranslation();
  const mutation = mutations.updateLoginPolicy;
  const busy = disabled || mutation.isPending;
  const settings = settingsQuery.data;
  const policy: LocalLoginPolicy = settings?.localLoginPolicy ?? "enabled";
  // A list that failed to arrive is not an empty list. Saying "no emergency
  // account is designated" because a request 500'd would send an operator to
  // designate a second one, or talk them into tightening a policy this card
  // cannot actually vouch for.
  const accountsUnknown = usersQuery.data === undefined;

  const designations = breakGlassDesignations(usersQuery.data);
  const qualifying = designations.filter(isQualifyingBreakGlass);
  const emergencyAccount = (qualifying[0] ?? designations[0])?.username ?? null;
  // A refusal names the account that would clear it; prefer the server's word.
  const refusedAccount = breakGlassAccountFromError(mutation.error) ?? emergencyAccount;
  const error = mutation.error ? organisationErrorMessage(mutation.error, t) : null;

  const save = (next: LocalLoginPolicy) => {
    if (!settings || next === policy) {
      return;
    }
    void mutation
      .mutateAsync(buildSettingsUpdateRequest(settings, { localLoginPolicy: next }))
      .catch(() => undefined);
  };

  return (
    <section id="organisation-login-policy" className="scroll-mt-16 space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <KeyRound className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{t("organisation.loginPolicy.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("organisation.loginPolicy.description")}</p>
        </div>
      </div>

      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      {settings === undefined ? (
        settingsQuery.isError ? (
          // Without the current value the select would offer "Everyone" as if
          // that were the saved policy, and saving it would be a relaxation
          // nobody asked for. Offer the retry instead of a control.
          <AlertMessage variant="error">
            <span className="flex flex-wrap items-center gap-2">
              {t("organisation.loginPolicy.loadFailed")}
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={settingsQuery.isFetching}
                onClick={() => void settingsQuery.refetch()}
              >
                {t("common.actions.retry")}
              </Button>
            </span>
          </AlertMessage>
        ) : (
          <SpinnerBlock />
        )
      ) : (
        <div className="space-y-1">
          <Label htmlFor="organisation-login-policy-select" className="text-xs font-medium">
            {t("organisation.loginPolicy.label")}
          </Label>
          <Select value={policy} disabled={busy} onValueChange={(value) => save(value as LocalLoginPolicy)}>
            <SelectTrigger id="organisation-login-policy-select" aria-label={t("organisation.loginPolicy.label")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {POLICIES.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`organisation.loginPolicy.options.${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[11px] text-muted-foreground">{t(`organisation.loginPolicy.help.${policy}`)}</p>
        </div>
      )}

      {canSeeAccounts && accountsUnknown ? (
        <AlertMessage variant="warning">
          {usersQuery.isError
            ? t("organisation.loginPolicy.accountsLoadFailed")
            : t("organisation.loginPolicy.accountsLoading")}
        </AlertMessage>
      ) : canSeeAccounts && qualifying.length === 0 ? (
        <AlertMessage variant="warning">
          {refusedAccount
            ? t("organisation.loginPolicy.enrolHint", { username: refusedAccount })
            : t("organisation.loginPolicy.noDesignation")}
        </AlertMessage>
      ) : null}

      <div className="grid gap-2 rounded-lg border p-3 sm:grid-cols-2" data-testid="organisation-emergency-facts">
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground">{t("organisation.loginPolicy.emergencyAccount")}</p>
          {!canSeeAccounts ? (
            <p className="text-sm">{t("organisation.loginPolicy.accountUnavailable")}</p>
          ) : accountsUnknown ? (
            <p className="text-sm">{t("organisation.loginPolicy.accountUnknown")}</p>
          ) : (
            <p className="font-mono text-sm font-medium select-all">{emergencyAccount ?? "-"}</p>
          )}
          {canSeeAccounts && !accountsUnknown && emergencyAccount ? (
            <p className="text-[11px] text-muted-foreground">
              {qualifying.length > 0
                ? t("organisation.loginPolicy.ready")
                : t("organisation.loginPolicy.notReady")}
            </p>
          ) : null}
        </div>
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground">{t("organisation.loginPolicy.emergencyUrl")}</p>
          <p className="font-mono text-sm font-medium break-all select-all">{emergencyUrl()}</p>
          <p className="text-[11px] text-muted-foreground">{t("organisation.loginPolicy.emergencyUrlHelp")}</p>
        </div>
      </div>
    </section>
  );
}

/** The absolute URL, because the point of it is to be pasted into a password manager. */
function emergencyUrl(): string {
  if (typeof window === "undefined") {
    return LOCAL_LOGIN_URL;
  }
  return `${window.location.origin}${LOCAL_LOGIN_URL}`;
}

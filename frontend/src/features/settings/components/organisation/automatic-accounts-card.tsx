import { Workflow } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SpinnerBlock } from "@/components/ui/spinner";
import type { AuthProvider, ScimTokenIssued } from "@/features/organisation/api";
import { organisationErrorMessage, useOrganisationMutations, useScimTokens } from "@/features/organisation/hooks";
import { companyLoginProvider } from "@/features/organisation/rules";
import { ORGANISATION_SCIM_ID } from "@/features/settings/advanced-settings-deeplink";

const MAX_LABEL_LENGTH = 64;

export type AutomaticAccountsCardProps = {
  /** The whole query: a list that failed to load must never read as "no credential exists". */
  tokensQuery: ReturnType<typeof useScimTokens>;
  /** The group's provider rows; the gate below is derived from them, never written inline here. */
  providers: readonly AuthProvider[] | undefined;
  mutations: ReturnType<typeof useOrganisationMutations>;
  disabled?: boolean;
};

/**
 * Letting the company's own sign-in service add and disable people here.
 *
 * The card is drawn on every install that can see this group, including the
 * one that cannot use it yet: the collapsed line already promises automatic
 * account management, so an operator who opens the group looking for it is
 * owed the reason it is off rather than a silence. The reason is a fact about
 * the install — no company sign-in is on — and it comes from a predicate in
 * the rules module, not from a condition written here.
 *
 * A freshly issued or rotated credential is shown once. It lives in this
 * component's state, above the list the issue itself replaces, and the refetch
 * and session refresh that would re-render the group around it are deferred
 * until the dialog is dismissed — the first credential flips the disclosure
 * tier, and a tier flip mid-copy would take the value off the screen.
 */
export function AutomaticAccountsCard({
  tokensQuery,
  providers,
  mutations,
  disabled = false,
}: AutomaticAccountsCardProps) {
  const { t } = useTranslation();
  const [label, setLabel] = useState("");
  // The plaintext, for exactly as long as the dialog is open.
  const [issued, setIssued] = useState<ScimTokenIssued | null>(null);
  const [refusal, setRefusal] = useState<unknown>(null);

  const companyLogin = companyLoginProvider(providers);
  const usable = companyLogin !== null;
  const busy = disabled || mutations.busy;
  const tokens = tokensQuery.data?.tokens ?? [];
  // Only the loaded answer knows where this install serves the endpoint. Until
  // it does — and after a failed load, which never will — the site origin on
  // its own is not an address, and a copy control offering it hands the
  // operator something their sign-in service cannot reach.
  const address = tokensQuery.data === undefined ? null : addressFor(tokensQuery.data.basePath);

  const show = (result: ScimTokenIssued) => {
    setRefusal(null);
    setIssued(result);
    // The plaintext is in this component's state now, so drop the copy the
    // mutation retained. `gcTime: 0` only disposes of a mutation nothing
    // observes any more, and these two are owned by the group above this card:
    // they stay observed for as long as the group is open.
    mutations.issueToken.reset();
    mutations.rotateToken.reset();
  };

  const issue = () => {
    setRefusal(null);
    void mutations.issueToken
      .mutateAsync({ label: label.trim() })
      .then((result) => {
        setLabel("");
        show(result);
      })
      .catch(setRefusal);
  };

  const rotate = (tokenId: string) => {
    setRefusal(null);
    void mutations.rotateToken.mutateAsync(tokenId).then(show).catch(setRefusal);
  };

  const revoke = (tokenId: string) => {
    setRefusal(null);
    void mutations.revokeToken.mutateAsync(tokenId).catch(setRefusal);
  };

  const dismiss = () => {
    setIssued(null);
    // Only now: the value is off the screen, so the list may be replaced and
    // the session may say that this install manages accounts automatically.
    void mutations.settleScimTokens();
  };

  return (
    <section id={ORGANISATION_SCIM_ID} className="scroll-mt-16 space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <Workflow className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{t("organisation.automaticAccounts.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("organisation.automaticAccounts.description")}</p>
        </div>
      </div>

      {refusal !== null ? (
        <AlertMessage variant="error">{organisationErrorMessage(refusal, t)}</AlertMessage>
      ) : null}

      {usable ? null : (
        <div data-testid="automatic-accounts-blocked">
          <AlertMessage variant="warning">{t("organisation.automaticAccounts.needsCompanyLogin")}</AlertMessage>
        </div>
      )}

      {usable && !companyLogin.linkByEmail ? (
        // The same person arriving twice — once pushed, once signing in — is
        // the surprise this surface is most likely to produce, and the setting
        // that prevents it lives one card up.
        <AlertMessage variant="warning">
          {t("organisation.automaticAccounts.separateAccountsHint", { label: companyLogin.label })}
        </AlertMessage>
      ) : null}

      {address === null ? null : (
        <div className="space-y-1 rounded-lg border p-3">
          <p className="text-xs text-muted-foreground">{t("organisation.automaticAccounts.address")}</p>
          <div className="flex min-w-0 items-center gap-2">
            <p
              className="min-w-0 flex-1 font-mono text-sm font-medium break-all select-all"
              data-testid="scim-base-url"
            >
              {address}
            </p>
            <CopyButton value={address} label={t("organisation.automaticAccounts.copyAddress")} />
          </div>
          <p className="text-[11px] text-muted-foreground">{t("organisation.automaticAccounts.addressHelp")}</p>
        </div>
      )}

      {tokensQuery.data === undefined ? (
        tokensQuery.isError ? (
          <AlertMessage variant="error">
            <span className="flex flex-wrap items-center gap-2">
              {t("organisation.automaticAccounts.loadFailed")}
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={tokensQuery.isFetching}
                onClick={() => void tokensQuery.refetch()}
              >
                {t("common.actions.retry")}
              </Button>
            </span>
          </AlertMessage>
        ) : (
          <SpinnerBlock />
        )
      ) : (
        <div className="space-y-2" data-testid="scim-token-list">
          {tokens.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("organisation.automaticAccounts.empty")}</p>
          ) : (
            tokens.map((token) => (
              <div key={token.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3">
                <div className="min-w-0 space-y-0.5">
                  <p className="truncate text-sm font-medium">{token.label}</p>
                  <p className="font-mono text-[11px] text-muted-foreground">{token.tokenPrefix}…</p>
                  <p className="text-[11px] text-muted-foreground">
                    {token.lastUsedAt
                      ? t("organisation.automaticAccounts.lastSync", {
                          when: new Date(token.lastUsedAt).toLocaleString(),
                        })
                      : t("organisation.automaticAccounts.neverSynced")}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={busy}
                    onClick={() => rotate(token.id)}
                  >
                    {t("organisation.automaticAccounts.actions.rotate")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={busy}
                    onClick={() => revoke(token.id)}
                  >
                    {t("organisation.automaticAccounts.actions.revoke")}
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      <div className="space-y-1">
        <Label htmlFor="organisation-scim-label" className="text-xs font-medium">
          {t("organisation.automaticAccounts.labelField")}
        </Label>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id="organisation-scim-label"
            className="max-w-xs"
            value={label}
            maxLength={MAX_LABEL_LENGTH}
            disabled={busy || !usable}
            placeholder={t("organisation.automaticAccounts.labelPlaceholder")}
            onChange={(event) => setLabel(event.target.value)}
          />
          <Button type="button" size="sm" disabled={busy || !usable || label.trim() === ""} onClick={issue}>
            {t("organisation.automaticAccounts.actions.issue")}
          </Button>
        </div>
      </div>

      <IssuedSecretDialog issued={issued} onClose={dismiss} />
    </section>
  );
}

/**
 * The one time the credential is on screen. Its value comes from the parent's
 * state and goes nowhere else: not into a query cache, not into storage, not
 * into a URL, and not into a mutation that outlives this dialog.
 */
function IssuedSecretDialog({ issued, onClose }: { issued: ScimTokenIssued | null; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <Dialog open={issued !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("organisation.automaticAccounts.secret.title")}</DialogTitle>
          <DialogDescription>
            {t("organisation.automaticAccounts.secret.description", { label: issued?.token.label ?? "" })}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">{t("organisation.automaticAccounts.secret.warning")}</p>
          <div className="flex min-w-0 items-center gap-2 overflow-hidden rounded-lg border bg-muted/20 px-3 py-2">
            <p className="min-w-0 flex-1 truncate font-mono text-xs" data-testid="scim-token-secret">
              {issued?.secret ?? ""}
            </p>
            <CopyButton
              value={issued?.secret ?? ""}
              label={t("organisation.automaticAccounts.secret.copy")}
            />
          </div>
        </div>
        <DialogFooter>
          <Button type="button" onClick={onClose}>
            {t("common.actions.done")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Absolute, because the point of it is to be pasted into somebody else's console. */
function addressFor(basePath: string): string {
  if (typeof window === "undefined") {
    return basePath;
  }
  return `${window.location.origin}${basePath}`;
}

import { AudioLines, Loader2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useOAuthLivePolicy } from "@/features/accounts/hooks/use-oauth-live-policy";
import type { OAuthLivePolicyUpdateRequest } from "@/features/accounts/schemas";
import { AccountMultiSelect } from "@/features/api-keys/components/account-multi-select";

export type OAuthLivePolicyCardProps = {
  accountId: string;
  readOnly?: boolean;
};

export function OAuthLivePolicyCard({
  accountId,
  readOnly = false,
}: OAuthLivePolicyCardProps) {
  const { t } = useTranslation();
  const { policyQuery, updateMutation } = useOAuthLivePolicy(accountId);
  const [draft, setDraft] = useState<OAuthLivePolicyUpdateRequest | null>(null);
  const policy = draft ?? policyQuery.data ?? { isActive: false, allowedAccountIds: [] };
  const { isActive, allowedAccountIds } = policy;

  const emptyActivePolicy = isActive && allowedAccountIds.length === 0;
  const busy = policyQuery.isLoading || updateMutation.isPending;

  return (
    <section className="min-w-0 rounded-lg border bg-muted/30 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <AudioLines className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">{t("accounts.oauthLive.title")}</h3>
            <p className="text-xs text-muted-foreground">
              {t("accounts.oauthLive.description")}
            </p>
          </div>
        </div>
        <Switch
          aria-label={t("accounts.oauthLive.enableAria")}
          checked={isActive}
          disabled={busy || readOnly || policyQuery.isError}
          onCheckedChange={(checked) => setDraft({ ...policy, isActive: checked })}
        />
      </div>

      <div className="mt-3 space-y-2">
        <label className="text-xs font-medium" htmlFor="oauth-live-allowed-accounts">
          {t("accounts.oauthLive.allowedAccounts")}
        </label>
        <AccountMultiSelect
          value={allowedAccountIds}
          onChange={(nextAllowedAccountIds) =>
            setDraft({ ...policy, allowedAccountIds: nextAllowedAccountIds })
          }
          selectionMode="explicit"
          placeholder={t("accounts.oauthLive.selectAccounts")}
          triggerId="oauth-live-allowed-accounts"
          disabled={busy || readOnly || policyQuery.isError}
        />
        {emptyActivePolicy ? (
          <p className="text-xs text-destructive" role="alert">
            {t("accounts.oauthLive.emptyError")}
          </p>
        ) : null}
        {policyQuery.isError ? (
          <p className="text-xs text-destructive" role="alert">
            {t("accounts.oauthLive.loadFailed")}
          </p>
        ) : null}
        <div className="flex justify-end">
          <Button
            type="button"
            size="sm"
            disabled={busy || readOnly || policyQuery.isError || emptyActivePolicy}
            onClick={() =>
              updateMutation.mutate(
                { isActive, allowedAccountIds },
                { onSuccess: () => setDraft(null) },
              )
            }
          >
            {updateMutation.isPending ? (
              <Loader2 className="mr-1 size-3 animate-spin" aria-hidden="true" />
            ) : null}
            {t("common.actions.save")}
          </Button>
        </div>
      </div>
    </section>
  );
}

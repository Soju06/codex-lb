import { AudioLines, Loader2, Radio, UsersRound } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { AccountMultiSelect } from "@/features/api-keys/components/account-multi-select";
import { useOAuthLivePolicy } from "@/features/settings/hooks/use-oauth-live-policy";
import type { OAuthLivePolicyUpdateRequest } from "@/features/settings/schemas";

export type OAuthLiveSettingsProps = {
  readOnly?: boolean;
};

export function OAuthLiveSettings({ readOnly = false }: OAuthLiveSettingsProps) {
  const { t } = useTranslation();
  const { policyQuery, updateMutation } = useOAuthLivePolicy();
  const [draft, setDraft] = useState<OAuthLivePolicyUpdateRequest | null>(null);
  const policy = draft ?? policyQuery.data ?? { isActive: false, allowedAccountIds: [] };
  const { isActive, allowedAccountIds } = policy;
  const emptyActivePolicy = isActive && allowedAccountIds.length === 0;
  const busy = policyQuery.isLoading || updateMutation.isPending;

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10">
          <AudioLines className="size-4 text-primary" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">{t("settings.oauthLive.title")}</h3>
            <span className="rounded-full border border-primary/20 bg-primary/5 px-2 py-0.5 text-[10px] font-medium text-primary">
              OAuth
            </span>
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("settings.oauthLive.description")}
          </p>
        </div>
      </div>

      <div className="mt-4 overflow-hidden rounded-xl border bg-muted/20">
        <div className="flex items-start justify-between gap-4 border-b bg-background/70 p-4">
          <div className="flex min-w-0 items-start gap-2.5">
            <Radio className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
            <div>
              <label className="text-xs font-semibold" htmlFor="oauth-live-enabled">
                {t("settings.oauthLive.toggle.label")}
              </label>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {t("settings.oauthLive.toggle.description")}
              </p>
            </div>
          </div>
          <Switch
            id="oauth-live-enabled"
            aria-label={t("settings.oauthLive.enableAria")}
            checked={isActive}
            disabled={busy || readOnly || policyQuery.isError}
            onCheckedChange={(checked) => setDraft({ ...policy, isActive: checked })}
          />
        </div>

        <div className="grid grid-cols-[1rem_minmax(0,1fr)] gap-x-2.5 gap-y-3 p-4">
          <UsersRound className="mt-0.5 size-4 text-primary" aria-hidden="true" />
          <div>
              <label className="text-xs font-semibold" htmlFor="oauth-live-allowed-accounts">
                {t("settings.oauthLive.pool.label")}
              </label>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {t("settings.oauthLive.pool.description")}
              </p>
          </div>

          <div className="col-start-2 min-w-0" data-slot="oauth-live-account-control">
            <AccountMultiSelect
              value={allowedAccountIds}
              onChange={(nextAllowedAccountIds) =>
                setDraft({ ...policy, allowedAccountIds: nextAllowedAccountIds })
              }
              selectionMode="explicit"
              presentation="compact"
              placeholder={t("settings.oauthLive.pool.placeholder")}
              triggerId="oauth-live-allowed-accounts"
              disabled={busy || readOnly || policyQuery.isError}
            />
          </div>

          {emptyActivePolicy ? (
            <p className="col-start-2 text-xs text-destructive" role="alert">
              {t("settings.oauthLive.emptyError")}
            </p>
          ) : null}
          {policyQuery.isError ? (
            <p className="col-start-2 text-xs text-destructive" role="alert">
              {t("settings.oauthLive.loadFailed")}
            </p>
          ) : null}

          <div className="col-start-2 flex items-center justify-between gap-3 border-t pt-3">
            <p className="text-[11px] text-muted-foreground">
              {t("settings.oauthLive.scopeNote")}
            </p>
            <Button
              type="button"
              size="sm"
              className="shrink-0"
              disabled={busy || readOnly || policyQuery.isError || emptyActivePolicy || draft === null}
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
      </div>
    </section>
  );
}

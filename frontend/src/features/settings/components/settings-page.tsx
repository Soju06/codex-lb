import { Suspense, lazy } from "react";
import { Settings } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { ApiKeysSection } from "@/features/api-keys/components/api-keys-section";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { FirewallSection } from "@/features/firewall/components/firewall-section";
import { QuotaPlannerSection } from "@/features/quota-planner/components/quota-planner-section";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { AppearanceSettings } from "@/features/settings/components/appearance-settings";
import { GuestAccessSettings } from "@/features/settings/components/guest-access-settings";
import { ImportSettings } from "@/features/settings/components/import-settings";
import { PasswordSettings } from "@/features/settings/components/password-settings";
import { RoutingSettings } from "@/features/settings/components/routing-settings";
import { SessionSettings } from "@/features/settings/components/session-settings";
import { SettingsSkeleton } from "@/features/settings/components/settings-skeleton";
import { UpstreamProxySettings } from "@/features/settings/components/upstream-proxy-settings";
import { StickySessionsSection } from "@/features/sticky-sessions/components/sticky-sessions-section";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useSettings, useUpstreamProxyAdmin } from "@/features/settings/hooks/use-settings";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";

const TotpSettings = lazy(() =>
  import("@/features/settings/components/totp-settings").then((m) => ({ default: m.TotpSettings })),
);

export function SettingsPage() {
  const { t } = useTranslation();
  const { settingsQuery, updateSettingsMutation } = useSettings();
  const { accountsQuery } = useAccounts();
  const {
    upstreamProxyQuery,
    createEndpointMutation,
    createPoolMutation,
    addPoolMemberMutation,
    testEndpointMutation,
  } = useUpstreamProxyAdmin();
  const authMode = useAuthStore((state) => state.authMode);
  const passwordManagementEnabled = useAuthStore((state) => state.passwordManagementEnabled);
  const passwordSessionActive = useAuthStore((state) => state.passwordSessionActive);
  const canWrite = useAuthStore((state) => state.canWrite);

  const settings = settingsQuery.data;
  const busy =
    updateSettingsMutation.isPending ||
    createEndpointMutation.isPending ||
    createPoolMutation.isPending ||
    addPoolMemberMutation.isPending ||
    testEndpointMutation.isPending;
  const controlsDisabled = busy || !canWrite;
  const error =
    getErrorMessageOrNull(settingsQuery.error) ||
    getErrorMessageOrNull(upstreamProxyQuery.error) ||
    getErrorMessageOrNull(updateSettingsMutation.error) ||
    getErrorMessageOrNull(createEndpointMutation.error) ||
    getErrorMessageOrNull(createPoolMutation.error) ||
    getErrorMessageOrNull(addPoolMemberMutation.error) ||
    getErrorMessageOrNull(testEndpointMutation.error);

  const handleSave = async (payload: SettingsUpdateRequest) => {
    await updateSettingsMutation.mutateAsync(payload);
  };

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Page header */}
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Settings className="h-5 w-5 text-primary" />
          {t("settings.page.title")}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("settings.page.subtitle")}</p>
      </div>

      {!settings ? (
        <SettingsSkeleton />
      ) : (
        <>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
          {!canWrite ? (
            <div className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-foreground">
              {t("settings.page.readOnlyNotice")}
            </div>
          ) : null}

          {authMode === "trusted_header" ? (
            <div className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-foreground">
              {t("settings.page.trustedHeaderNotice")}
            </div>
          ) : null}

          {authMode === "disabled" ? (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs font-medium text-foreground">
              {t("settings.page.disabledNotice")}
            </div>
          ) : null}

          <div className="space-y-4">
            <AppearanceSettings />
            <RoutingSettings
              key={[
                settings.openaiCacheAffinityMaxAgeSeconds,
                settings.warmupModel,
                settings.limitWarmupModel,
                settings.limitWarmupPrompt,
                settings.limitWarmupExhaustedThresholdPercent,
                settings.limitWarmupCooldownSeconds,
                settings.limitWarmupStaggeredIdleEnabled,
              ].join(":")}
              settings={settings}
              accounts={accountsQuery.data ?? []}
              accountsLoading={accountsQuery.isLoading}
              busy={controlsDisabled}
              onSave={handleSave}
            />
            {upstreamProxyQuery.data ? (
              <UpstreamProxySettings
                admin={upstreamProxyQuery.data}
                busy={controlsDisabled}
                onSaveSettings={handleSave}
                onCreateEndpoint={(payload) => createEndpointMutation.mutateAsync(payload)}
                onTestEndpoint={(endpointId) => testEndpointMutation.mutateAsync(endpointId)}
                onCreatePool={(payload) => createPoolMutation.mutateAsync(payload)}
                onAddPoolMember={(poolId, payload) =>
                  addPoolMemberMutation.mutateAsync({ poolId, payload })
                }
              />
            ) : null}
            <ImportSettings settings={settings} busy={controlsDisabled} onSave={handleSave} />
            {canWrite ? (
              <GuestAccessSettings
                settings={settings}
                busy={busy}
                onSave={handleSave}
                onRefresh={() => settingsQuery.refetch()}
              />
            ) : null}
            {canWrite ? <PasswordSettings disabled={busy} /> : null}
            {canWrite && passwordManagementEnabled ? (
              <SessionSettings settings={settings} busy={busy} onSave={handleSave} />
            ) : null}
            {canWrite && passwordManagementEnabled && passwordSessionActive ? (
              <Suspense fallback={null}>
                <TotpSettings settings={settings} disabled={busy} onSave={handleSave} />
              </Suspense>
            ) : null}

            <ApiKeysSection
              apiKeyAuthEnabled={settings.apiKeyAuthEnabled}
              hideUpstreamQuotaFromApiKeys={settings.hideUpstreamQuotaFromApiKeys}
              disabled={controlsDisabled}
              onApiKeyAuthEnabledChange={(enabled) =>
                void handleSave(buildSettingsUpdateRequest(settings, { apiKeyAuthEnabled: enabled }))
              }
              onHideUpstreamQuotaFromApiKeysChange={(enabled) =>
                void handleSave(buildSettingsUpdateRequest(settings, { hideUpstreamQuotaFromApiKeys: enabled }))
              }
            />
            <FirewallSection disabled={controlsDisabled} />
            <QuotaPlannerSection disabled={controlsDisabled} />
            <StickySessionsSection disabled={controlsDisabled} />
          </div>

          <LoadingOverlay visible={!!settings && busy} label={t("settings.page.savingLabel")} />
        </>
      )}
    </div>
  );
}

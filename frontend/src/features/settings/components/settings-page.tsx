import { Suspense, lazy } from "react";
import { Settings } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { ApiKeysSection } from "@/features/api-keys/components/api-keys-section";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { FirewallSection } from "@/features/firewall/components/firewall-section";
import { QuotaPlannerSection } from "@/features/quota-planner/components/quota-planner-section";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { AppearanceSettings } from "@/features/settings/components/appearance-settings";
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
  const { settingsQuery, updateSettingsMutation } = useSettings();
  const { accountsQuery } = useAccounts();
  const {
    upstreamProxyQuery,
    createEndpointMutation,
    createPoolMutation,
    addPoolMemberMutation,
  } = useUpstreamProxyAdmin();
  const authMode = useAuthStore((state) => state.authMode);
  const passwordManagementEnabled = useAuthStore((state) => state.passwordManagementEnabled);
  const passwordSessionActive = useAuthStore((state) => state.passwordSessionActive);

  const settings = settingsQuery.data;
  const busy =
    updateSettingsMutation.isPending ||
    createEndpointMutation.isPending ||
    createPoolMutation.isPending ||
    addPoolMemberMutation.isPending;
  const error =
    getErrorMessageOrNull(settingsQuery.error) ||
    getErrorMessageOrNull(upstreamProxyQuery.error) ||
    getErrorMessageOrNull(updateSettingsMutation.error) ||
    getErrorMessageOrNull(createEndpointMutation.error) ||
    getErrorMessageOrNull(createPoolMutation.error) ||
    getErrorMessageOrNull(addPoolMemberMutation.error);

  const handleSave = async (payload: SettingsUpdateRequest) => {
    await updateSettingsMutation.mutateAsync(payload);
  };

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Page header */}
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Settings className="h-5 w-5 text-primary" />
          Settings
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">Configure routing, auth, API key management, and firewall.</p>
      </div>

      {!settings ? (
        <SettingsSkeleton />
      ) : (
        <>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

          {authMode === "trusted_header" ? (
            <div className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-foreground">
              Dashboard access is authenticated by a trusted reverse-proxy header. Password and TOTP stay
              available only as optional fallback login.
            </div>
          ) : null}

          {authMode === "disabled" ? (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs font-medium text-foreground">
              Dashboard auth is fully bypassed by configuration. Only use this mode behind network restrictions
              or external access control.
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
                settings.limitWarmupCooldownSeconds,
              ].join(":")}
              settings={settings}
              accounts={accountsQuery.data ?? []}
              accountsLoading={accountsQuery.isLoading}
              busy={busy}
              onSave={handleSave}
            />
            {upstreamProxyQuery.data ? (
              <UpstreamProxySettings
                admin={upstreamProxyQuery.data}
                busy={busy}
                onSaveSettings={handleSave}
                onCreateEndpoint={(payload) => createEndpointMutation.mutateAsync(payload)}
                onCreatePool={(payload) => createPoolMutation.mutateAsync(payload)}
                onAddPoolMember={(poolId, payload) =>
                  addPoolMemberMutation.mutateAsync({ poolId, payload })
                }
              />
            ) : null}
            <ImportSettings settings={settings} busy={busy} onSave={handleSave} />
            <PasswordSettings disabled={busy} />
            {passwordManagementEnabled ? (
              <SessionSettings settings={settings} busy={busy} onSave={handleSave} />
            ) : null}
            {passwordManagementEnabled && passwordSessionActive ? (
              <Suspense fallback={null}>
                <TotpSettings settings={settings} disabled={busy} onSave={handleSave} />
              </Suspense>
            ) : null}

            <ApiKeysSection
              apiKeyAuthEnabled={settings.apiKeyAuthEnabled}
              disabled={busy}
              onApiKeyAuthEnabledChange={(enabled) =>
                void handleSave(buildSettingsUpdateRequest(settings, { apiKeyAuthEnabled: enabled }))
              }
            />
            <FirewallSection />
            <QuotaPlannerSection />
            <StickySessionsSection />
          </div>

          <LoadingOverlay visible={!!settings && busy} label="Saving settings..." />
        </>
      )}
    </div>
  );
}

import { useState } from "react";
import { Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";

import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { Button } from "@/components/ui/button";
import { ApiKeysSection } from "@/features/api-keys/components/api-keys-section";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { FirewallSection } from "@/features/firewall/components/firewall-section";
import { ModelSourcesSettings } from "@/features/model-sources/components/model-sources-settings";
import { useModelSources } from "@/features/model-sources/hooks/use-model-sources";
import { QuotaPlannerSection } from "@/features/quota-planner/components/quota-planner-section";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { shouldExpandAdvancedSettings } from "@/features/settings/advanced-settings-deeplink";
import { AccessCard } from "@/features/settings/components/access/access-card";
import { AdvancedSettingsGroup } from "@/features/settings/components/advanced-settings-group";
import { OrganisationSettingsGroup } from "@/features/settings/components/organisation/organisation-group";
import { AppearanceSettings } from "@/features/settings/components/appearance-settings";
import { ConversationArchiveSettings } from "@/features/settings/components/conversation-archive-settings";
import { DataRetentionSettings } from "@/features/settings/components/data-retention-settings";
import { ImportSettings } from "@/features/settings/components/import-settings";
import { ModelCatalogueSettings } from "@/features/settings/components/model-catalogue-settings";
import { ResetCreditSettings } from "@/features/settings/components/reset-credit-settings";
import { ResilienceSettings } from "@/features/settings/components/resilience-settings";
import { SessionBridgeSettings } from "@/features/settings/components/session-bridge-settings";
import { BackgroundJobsSettings } from "@/features/settings/components/background-jobs-settings";
import { RoutingSettings } from "@/features/settings/components/routing-settings";
import { UpstreamTimeoutSettings } from "@/features/settings/components/upstream-timeout-settings";
import { SettingsSkeleton } from "@/features/settings/components/settings-skeleton";
import { TelemetrySettings } from "@/features/settings/components/telemetry-settings";
import { UpstreamProxySettings } from "@/features/settings/components/upstream-proxy-settings";
import { CacheIsolationProbeSection } from "@/features/cache-probe/components/cache-isolation-probe-section";
import { StickySessionsSection } from "@/features/sticky-sessions/components/sticky-sessions-section";
import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
import { useSettings, useUpstreamProxyAdmin } from "@/features/settings/hooks/use-settings";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";

// C2-2 routing/overload: a layer move (dashboard <-> inherited) without a
// value change must still reset the routing form's drafts.
const ROUTING_OVERLOAD_PROVENANCE_KEYS = [
  "proxy_overload_isolation_seconds",
  "proxy_account_error_rate_weighting_enabled",
  "proxy_account_inflight_penalty_pct",
  "proxy_account_lease_token_weight",
  "proxy_account_lease_ttl_seconds",
] as const;

const FIREWALL_LAYOUT_QUERY_KEYS = [
  ["accounts", "list"],
  ["settings", "upstream-proxy"],
  ["model-sources", "list"],
  // M4 model catalogue: the card sits above Firewall and grows by a table row
  // per override, so a late response would push a #firewall scroll out of view.
  ["settings", "model-context-window-overrides"],
] as const;

export function SettingsPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const expandAdvanced = shouldExpandAdvancedSettings(location.search, location.hash);
  const advancedScrollToId = location.hash.replace(/^#/, "") || undefined;
  const { settingsQuery, updateSettingsMutation } = useSettings();
  const [initialRetryError, setInitialRetryError] = useState<string | null>(null);
  const { accountsQuery } = useAccounts();
  const { modelSourcesQuery } = useModelSources();
  const authMode = useAuthStore((state) => state.authMode);
  const canWrite = useAuthStore((state) => state.canWrite);
  // Security-bearing controls (API-key auth policy, firewall, proxy endpoints)
  // need `security:write`; an Operator sees them read-only instead of a 403.
  const canWriteSecurity = usePermission("security:write");
  // The probe spends account quota, so it mirrors its backend `ops:write` gate
  // rather than the coarse write alias.
  const canWriteOps = usePermission("ops:write");
  // A fully signed-in account without `write` (a Viewer) still owns its password and two-factor.
  // Any signed-in account reaches its own password/two-factor controls: a
  // reverse-proxy account has no password session and still needs to enrol a
  // second factor, which is how it confirms sensitive changes.
  const personalSignIn = useAuthStore(
    (state) => state.passwordManagementEnabled && (state.passwordSessionActive || state.user !== null),
  );
  // API keys, upstream-proxy administration, and sticky sessions are write-only
  // reads on the backend (403 for guests), so they are not mounted or fetched
  // without write access. `enabled: false` only stops fetching; cached data from
  // an earlier admin session is still returned, so rendering is gated on
  // `canWrite` as well.
  const {
    upstreamProxyQuery,
    createEndpointMutation,
    createPoolMutation,
    addPoolMemberMutation,
    testEndpointMutation,
  } = useUpstreamProxyAdmin({ enabled: canWrite });

  const settings = settingsQuery.data;
  const busy =
    updateSettingsMutation.isPending ||
    createEndpointMutation.isPending ||
    createPoolMutation.isPending ||
    addPoolMemberMutation.isPending ||
    testEndpointMutation.isPending;
  const controlsDisabled = busy || !canWrite;
  const settingsLoadError = getErrorMessageOrNull(
    settingsQuery.error,
    t("settings.toasts.loadFailed"),
  );
  const displayedSettingsLoadError = settingsLoadError || initialRetryError;
  // With no settings loaded the failed-load branch below owns this message, so
  // the page-level alert would otherwise render it a second time.
  const error =
    (settings ? settingsLoadError : null) ||
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

      {settingsQuery.isPending && !settings && initialRetryError === null ? (
        <SettingsSkeleton />
      ) : !settings ? (
        <div className="space-y-3 rounded-xl border bg-card p-4">
          <div role="alert">
            <AlertMessage variant="error">
              {displayedSettingsLoadError || t("settings.toasts.loadFailed")}
            </AlertMessage>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setInitialRetryError(displayedSettingsLoadError || t("settings.toasts.loadFailed"));
              void settingsQuery.refetch().finally(() => {
                setInitialRetryError(null);
              });
            }}
            disabled={settingsQuery.isFetching || initialRetryError !== null}
          >
            {t("common.actions.retry")}
          </Button>
        </div>
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
            <ImportSettings settings={settings} busy={controlsDisabled} onSave={handleSave} />
            <ResetCreditSettings settings={settings} busy={controlsDisabled} onSave={handleSave} />
            {/* Guest access, password, session and TOTP live inside the Access
                card. It mounts for `write` holders and for any fully signed-in
                account (its own password/TOTP); guests never saw it and still do not. */}
            {canWrite || personalSignIn ? (
              <AccessCard
                settings={settings}
                busy={busy}
                onSave={handleSave}
                onRefresh={() => settingsQuery.refetch()}
              />
            ) : null}

            {canWrite ? (
              <ApiKeysSection
                apiKeyAuthEnabled={settings.apiKeyAuthEnabled}
                hideUpstreamQuotaFromApiKeys={settings.hideUpstreamQuotaFromApiKeys}
                disabled={controlsDisabled}
                policyControlsDisabled={controlsDisabled || !canWriteSecurity}
                onApiKeyAuthEnabledChange={(enabled) =>
                  void handleSave(buildSettingsUpdateRequest(settings, { apiKeyAuthEnabled: enabled }))
                }
                onHideUpstreamQuotaFromApiKeysChange={(enabled) =>
                  void handleSave(buildSettingsUpdateRequest(settings, { hideUpstreamQuotaFromApiKeys: enabled }))
                }
              />
            ) : null}

            <TelemetrySettings disabled={controlsDisabled} />

            <AdvancedSettingsGroup
              key={expandAdvanced ? `open:${advancedScrollToId ?? ""}` : "closed"}
              defaultOpen={expandAdvanced}
              scrollToId={advancedScrollToId}
              waitForQueryKeys={FIREWALL_LAYOUT_QUERY_KEYS}
            >
              <RoutingSettings
                key={[
                  settings.openaiCacheAffinityMaxAgeSeconds,
                  settings.warmupModel,
                  settings.limitWarmupModel,
                  settings.limitWarmupPrompt,
                  settings.limitWarmupExhaustedThresholdPercent,
                  settings.limitWarmupIdleThresholdPercent,
                  settings.limitWarmupCooldownSeconds,
                  settings.limitWarmupStaggeredIdleEnabled,
                   settings.proxyAccountResponseCreateLimit,
                   settings.proxyAccountResponseCreateLimitOverride,
                   settings.proxyAccountStreamLimit,
                   settings.proxyAccountStreamLimitOverride,
                   settings.proxyAccountStreamRecoveryReserve,
                   settings.proxyAccountStreamRecoveryReserveOverride,
                   settings.proxyApiKeyFairShareCongestionThresholdPct,
                   settings.proxyApiKeyFairShareCongestionThresholdPctOverride,
                   settings.proxyOverloadIsolationSeconds,
                   settings.proxyAccountErrorRateWeightingEnabled,
                   settings.proxyAccountInflightPenaltyPct,
                   settings.proxyAccountLeaseTokenWeight,
                   settings.proxyAccountLeaseTtlSeconds,
                   ...ROUTING_OVERLOAD_PROVENANCE_KEYS.map((name) => settings.provenance?.[name]?.source ?? ""),
                ].join(":")}
                settings={settings}
                accounts={accountsQuery.data ?? []}
                accountsLoading={accountsQuery.isLoading}
                modelSources={modelSourcesQuery.data?.sources ?? []}
                modelSourcesLoading={modelSourcesQuery.isLoading}
                modelSourcesError={modelSourcesQuery.error !== null}
                busy={controlsDisabled}
                onSave={handleSave}
              />
              <ResilienceSettings settings={settings} busy={controlsDisabled} onSave={handleSave} />
              <SessionBridgeSettings settings={settings} busy={controlsDisabled} onSave={handleSave} />
              <BackgroundJobsSettings settings={settings} busy={controlsDisabled} onSave={handleSave} />
              {canWrite && upstreamProxyQuery.data ? (
                <UpstreamProxySettings
                  admin={upstreamProxyQuery.data}
                  busy={controlsDisabled}
                  canCreateEndpoint={canWriteSecurity}
                  onSaveSettings={handleSave}
                  onCreateEndpoint={(payload) => createEndpointMutation.mutateAsync(payload)}
                  onTestEndpoint={(endpointId) => testEndpointMutation.mutateAsync(endpointId)}
                  onCreatePool={(payload) => createPoolMutation.mutateAsync(payload)}
                  onAddPoolMember={(poolId, payload) =>
                    addPoolMemberMutation.mutateAsync({ poolId, payload })
                  }
                />
              ) : null}
              <ModelSourcesSettings disabled={controlsDisabled} />
              <ModelCatalogueSettings disabled={controlsDisabled} />
              <FirewallSection disabled={controlsDisabled || !canWriteSecurity} />
              <QuotaPlannerSection disabled={controlsDisabled} />
              {canWrite ? <StickySessionsSection disabled={controlsDisabled} /> : null}
              {/* Next to sticky sessions: both answer "which account served this, and what did it reuse?" */}
              {canWriteOps ? <CacheIsolationProbeSection disabled={controlsDisabled} /> : null}
              <DataRetentionSettings
                key={[
                  settings.requestLogRetentionOverrideDays,
                  settings.usageHistoryRetentionOverrideDays,
                  settings.requestLogRetentionDays,
                  settings.usageHistoryRetentionDays,
                  // R2 spool retention: a saved value (or a layer move back to
                  // inherited) must re-seed the card's draft.
                  settings.httpResponsesSessionBridgeOperationSpoolRetentionSeconds,
                  settings.provenance?.http_responses_session_bridge_operation_spool_retention_seconds?.source ?? "",
                ].join(":")}
                settings={settings}
                busy={controlsDisabled}
                onSave={handleSave}
              />
              {/* M5 conversation archive: next to Data retention (same "what we keep" family). */}
              <ConversationArchiveSettings settings={settings} busy={controlsDisabled} onSave={handleSave} />
              <UpstreamTimeoutSettings
                key={[
                  settings.version,
                  settings.upstreamConnectTimeoutSeconds,
                  settings.proxyRequestBudgetSeconds,
                  settings.compactRequestBudgetSeconds,
                  settings.transcriptionRequestBudgetSeconds,
                  settings.httpResponsesStreamRequestBudgetSeconds,
                  settings.httpResponsesSessionBridgeRequestBudgetSeconds,
                  settings.streamIdleTimeoutSeconds,
                  settings.proxyDownstreamWebsocketIdleTimeoutSeconds,
                  settings.sseKeepaliveIntervalSeconds,
                ].join(":")}
                settings={settings}
                busy={controlsDisabled}
                onSave={handleSave}
              />
            </AdvancedSettingsGroup>

            {/* PR-2c-2: the second collapsed group, last on the page. It draws
                one line until a company install configures something, and its
                children (and their requests) only exist while it is open. */}
            <OrganisationSettingsGroup disabled={controlsDisabled} />
          </div>

          <LoadingOverlay visible={!!settings && busy} label={t("settings.page.savingLabel")} />
        </>
      )}
    </div>
  );
}

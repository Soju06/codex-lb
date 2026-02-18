import { lazy } from "react";
import { Settings } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { ApiKeysSection } from "@/features/api-keys/components/api-keys-section";
import { AppearanceSettings } from "@/features/settings/components/appearance-settings";
import { ImportSettings } from "@/features/settings/components/import-settings";
import { PasswordSettings } from "@/features/settings/components/password-settings";
import { RoutingSettings } from "@/features/settings/components/routing-settings";
import { SettingsSkeleton } from "@/features/settings/components/settings-skeleton";
import { useSettings } from "@/features/settings/hooks/use-settings";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";

const TotpSettings = lazy(() =>
  import("@/features/settings/components/totp-settings").then((m) => ({ default: m.TotpSettings })),
);

export function SettingsPage() {
  const { settingsQuery, updateSettingsMutation } = useSettings();

  const settings = settingsQuery.data;
  const busy = updateSettingsMutation.isPending;
  const error = getErrorMessageOrNull(settingsQuery.error) || getErrorMessageOrNull(updateSettingsMutation.error);

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
        <p className="mt-1 text-sm text-muted-foreground">Configure routing, auth, and API key management.</p>
      </div>

      {!settings ? (
        <SettingsSkeleton />
      ) : (
        <>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

          <div className="space-y-4">
            <AppearanceSettings />
            <RoutingSettings
              settings={settings}
              busy={busy}
              onSave={handleSave}
            />
            <ImportSettings settings={settings} busy={busy} onSave={handleSave} />
            <PasswordSettings disabled={busy} />
            <TotpSettings settings={settings} disabled={busy} onSave={handleSave} />

            <ApiKeysSection
              apiKeyAuthEnabled={settings.apiKeyAuthEnabled}
              disabled={busy}
              onApiKeyAuthEnabledChange={(enabled) =>
                void handleSave({
                  stickyThreadsEnabled: settings.stickyThreadsEnabled,
                  preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
                  importWithoutOverwrite: settings.importWithoutOverwrite,
                  totpRequiredOnLogin: settings.totpRequiredOnLogin,
                  apiKeyAuthEnabled: enabled,
                })
              }
            />
          </div>

          <LoadingOverlay visible={!!settings && busy} label="Saving settings..." />
        </>
      )}
    </div>
  );
}

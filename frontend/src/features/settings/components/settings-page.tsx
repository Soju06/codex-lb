import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { ApiKeysSection } from "@/features/api-keys/components/api-keys-section";
import { PasswordSettings } from "@/features/settings/components/password-settings";
import { RoutingSettings } from "@/features/settings/components/routing-settings";
import { TotpSettings } from "@/features/settings/components/totp-settings";
import { useSettings } from "@/features/settings/hooks/use-settings";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";

function getErrorMessage(error: unknown): string | null {
  if (!error) {
    return null;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Settings request failed";
}

export function SettingsPage() {
  const { settingsQuery, updateSettingsMutation } = useSettings();

  const settings = settingsQuery.data;
  const busy = settingsQuery.isFetching || updateSettingsMutation.isPending;
  const error = getErrorMessage(settingsQuery.error) || getErrorMessage(updateSettingsMutation.error);

  const handleSave = async (payload: SettingsUpdateRequest) => {
    await updateSettingsMutation.mutateAsync(payload);
  };

  if (!settings) {
    return (
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">Loading settings...</p>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">Configure routing, auth, and API key management.</p>
      </div>

      {error ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}

      <RoutingSettings
        key={`${settings.stickyThreadsEnabled}:${settings.preferEarlierResetAccounts}`}
        settings={settings}
        busy={busy}
        onSave={handleSave}
      />
      <PasswordSettings disabled={busy} />
      <TotpSettings settings={settings} disabled={busy} onSave={handleSave} />

      <ApiKeysSection
        apiKeyAuthEnabled={settings.apiKeyAuthEnabled}
        disabled={busy}
        onApiKeyAuthEnabledChange={(enabled) =>
          void handleSave({
            stickyThreadsEnabled: settings.stickyThreadsEnabled,
            preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
            totpRequiredOnLogin: settings.totpRequiredOnLogin,
            apiKeyAuthEnabled: enabled,
          })
        }
      />

      <LoadingOverlay visible={busy} label="Saving settings..." />
    </section>
  );
}

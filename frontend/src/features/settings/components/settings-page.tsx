import { lazy } from "react";
import { Settings } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { ApiKeysSection } from "@/features/api-keys/components/api-keys-section";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { AppearanceSettings } from "@/features/settings/components/appearance-settings";
import { ImportSettings } from "@/features/settings/components/import-settings";
import { PasswordSettings } from "@/features/settings/components/password-settings";
import { RoutingSettings } from "@/features/settings/components/routing-settings";
import { SettingsSkeleton } from "@/features/settings/components/settings-skeleton";
import { useSettings } from "@/features/settings/hooks/use-settings";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";
import { useUsers } from "@/features/users/hooks/use-users";
import { getErrorMessageOrNull } from "@/utils/errors";

const TotpSettings = lazy(() =>
  import("@/features/settings/components/totp-settings").then((m) => ({ default: m.TotpSettings })),
);

export function SettingsPage() {
  const role = useAuthStore((state) => state.user?.role);
  const isAdmin = role === "admin";
  const { settingsQuery, updateSettingsMutation } = useSettings(isAdmin);
  const usersQuery = useUsers({}, isAdmin).usersQuery;

  const settings = settingsQuery.data;
  const busy = isAdmin && updateSettingsMutation.isPending;
  const error = isAdmin
    ? getErrorMessageOrNull(settingsQuery.error) || getErrorMessageOrNull(updateSettingsMutation.error)
    : null;

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
        <p className="mt-1 text-sm text-muted-foreground">
          {isAdmin
            ? "Configure routing, auth, and API key management."
            : "Manage your account security and API keys."}
        </p>
      </div>

      {isAdmin && !settings ? (
        <SettingsSkeleton />
      ) : (
        <>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

          <div className="space-y-4">
            <AppearanceSettings />
            {isAdmin && settings ? (
              <>
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
                  ownerOptions={(usersQuery.data ?? []).map((user) => ({
                    id: user.id,
                    username: user.username,
                  }))}
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
              </>
            ) : (
              <>
                <PasswordSettings disabled={false} />
                <ApiKeysSection
                  apiKeyAuthEnabled={false}
                  disabled={false}
                  showApiKeyAuthToggle={false}
                  onApiKeyAuthEnabledChange={() => {}}
                />
              </>
            )}
          </div>

          <LoadingOverlay visible={Boolean(settings) && busy} label="Saving settings..." />
        </>
      )}
    </div>
  );
}

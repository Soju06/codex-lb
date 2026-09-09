import { Suspense, lazy } from "react";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { GuestAccessSettings } from "@/features/settings/components/guest-access-settings";
import { PasswordSettings } from "@/features/settings/components/password-settings";
import { SessionSettings } from "@/features/settings/components/session-settings";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

const TotpSettings = lazy(() =>
  import("@/features/settings/components/totp-settings").then((m) => ({ default: m.TotpSettings })),
);

export type AccessMySignInTabProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
  onRefresh: () => Promise<unknown>;
};

/**
 * The signed-in person's own controls: the four sections the Settings page
 * rendered at top level before the Access card existed, in the same order and
 * behind the same gates (guest access and session need `write`; TOTP needs a
 * password-backed session).
 */
export function AccessMySignInTab({ settings, busy, onSave, onRefresh }: AccessMySignInTabProps) {
  const canWrite = useAuthStore((state) => state.canWrite);
  const passwordManagementEnabled = useAuthStore((state) => state.passwordManagementEnabled);
  const passwordSessionActive = useAuthStore((state) => state.passwordSessionActive);

  return (
    <div className="space-y-4">
      {canWrite ? <GuestAccessSettings settings={settings} busy={busy} onSave={onSave} onRefresh={onRefresh} /> : null}
      {canWrite ? <PasswordSettings disabled={busy} /> : null}
      {canWrite && passwordManagementEnabled ? <SessionSettings settings={settings} busy={busy} onSave={onSave} /> : null}
      {canWrite && passwordManagementEnabled && passwordSessionActive ? (
        <Suspense fallback={null}>
          <TotpSettings settings={settings} disabled={busy} onSave={onSave} />
        </Suspense>
      ) : null}
    </div>
  );
}

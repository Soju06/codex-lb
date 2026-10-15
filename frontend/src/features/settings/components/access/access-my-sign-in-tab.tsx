import { Suspense, lazy } from "react";

import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
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
 * rendered at top level before the Access card existed, in the same order.
 * Guest access, session length and the TOTP requirement toggle are security
 * settings (`security:write`). The person's own password and TOTP secret belong
 * to any fully signed-in account (a Viewer included): they follow
 * `passwordManagementEnabled && passwordSessionActive`; the password card also
 * renders for the implicit admin holding `write`, who has no password yet.
 * A reverse-proxy account has no password session and still needs both: its
 * TOTP secret is how it confirms sensitive changes.
 */
export function AccessMySignInTab({ settings, busy, onSave, onRefresh }: AccessMySignInTabProps) {
  const canWrite = useAuthStore((state) => state.canWrite);
  const canWriteSecurity = usePermission("security:write");
  const passwordManagementEnabled = useAuthStore((state) => state.passwordManagementEnabled);
  const personal = useAuthStore(
    (state) => state.passwordManagementEnabled && (state.passwordSessionActive || state.user !== null),
  );

  return (
    <div className="space-y-4">
      {canWriteSecurity ? (
        <GuestAccessSettings settings={settings} busy={busy} onSave={onSave} onRefresh={onRefresh} />
      ) : null}
      {canWrite || personal ? <PasswordSettings disabled={busy} /> : null}
      {canWriteSecurity && passwordManagementEnabled ? (
        <SessionSettings settings={settings} busy={busy} onSave={onSave} />
      ) : null}
      {personal ? (
        <Suspense fallback={null}>
          <TotpSettings settings={settings} disabled={busy} canEditPolicy={canWriteSecurity} onSave={onSave} />
        </Suspense>
      ) : null}
    </div>
  );
}

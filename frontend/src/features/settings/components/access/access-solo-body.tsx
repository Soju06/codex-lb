import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
import { AccessMySignInTab, type AccessMySignInTabProps } from "@/features/settings/components/access/access-my-sign-in-tab";
import { PasswordSetupDialog } from "@/features/settings/components/password-setup-dialog";

export type AccessSoloBodyProps = AccessMySignInTabProps & {
  /** Opens the invite dialog the card owns (it must outlive the tier flip). */
  onInvite: () => void;
};

/**
 * Individual tier: one line that says this dashboard has a single person and
 * offers the way to a second one, then today's four controls unchanged. The
 * copy deliberately avoids the words user, role, SSO, SCIM, IdP and RBAC.
 */
export function AccessSoloBody({ onInvite, ...props }: AccessSoloBodyProps) {
  const { t } = useTranslation();
  const canManageUsers = usePermission("users:manage");
  const authMode = useAuthStore((state) => state.authMode);
  const user = useAuthStore((state) => state.user);
  const [setupOpen, setSetupOpen] = useState(false);

  // Only an account can invite (`409 admin_account_required`): a password
  // account or one the reverse proxy created. The implicit local admin is told
  // to set a password first; the disabled-auth principal has no account and
  // no way to get one, so the line is not rendered there at all.
  const showPeopleLine = canManageUsers && authMode !== "disabled";
  const needsPassword = user === null;

  return (
    <div className="space-y-4">
      {showPeopleLine ? (
        <div
          data-testid="access-solo-line"
          className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
        >
          <p className="text-sm text-muted-foreground">
            {needsPassword ? t("access.solo.passwordFirst") : t("access.solo.onlyYou")}
          </p>
          {needsPassword ? (
            <PasswordSetupDialog open={setupOpen} onOpenChange={setSetupOpen} disabled={props.busy}>
              <Button type="button" size="sm" className="h-8 shrink-0 text-xs" disabled={props.busy}>
                {t("access.solo.setPassword")}
              </Button>
            </PasswordSetupDialog>
          ) : (
            <Button type="button" size="sm" className="h-8 shrink-0 text-xs" disabled={props.busy} onClick={onInvite}>
              {t("access.solo.invite")}
            </Button>
          )}
        </div>
      ) : null}
      <AccessMySignInTab {...props} />
    </div>
  );
}

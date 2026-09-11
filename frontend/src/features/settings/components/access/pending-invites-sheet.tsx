import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { AlertMessage } from "@/components/alert-message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { DashboardRole, PendingInvite } from "@/features/access/api";
import { accessErrorMessage, useAccessMutations } from "@/features/access/hooks";
import type { IssuedLink } from "@/features/settings/components/access/invite-dialog";
import { formatExpiresIn } from "@/utils/formatters";

export type PendingInvitesSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  invites: readonly PendingInvite[];
  roles: readonly DashboardRole[];
  onIssued: (issued: IssuedLink) => void;
};

/**
 * Links nobody has used yet: resend (rotates the token) or revoke (removes the
 * row). Errors are shown inside the sheet — a banner behind the overlay would
 * be invisible.
 */
export function PendingInvitesSheet({ open, onOpenChange, invites, roles, onIssued }: PendingInvitesSheetProps) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const mutations = useAccessMutations({
    onMutate: () => setError(null),
    onError: (caught) => setError(accessErrorMessage(caught, t)),
  });
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) setError(null);
        onOpenChange(next);
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{t("access.pending.title")}</SheetTitle>
          <SheetDescription>{t("access.pending.description")}</SheetDescription>
        </SheetHeader>
        <div className="space-y-2 px-4 pb-4">
          {error ? (
            <div role="alert">
              <AlertMessage variant="error">{error}</AlertMessage>
            </div>
          ) : null}
          {invites.length === 0 ? <p className="text-sm text-muted-foreground">{t("access.pending.empty")}</p> : null}
          {invites.map((invite) => {
            const expiresIn = invite.expiresAt ? formatExpiresIn(invite.expiresAt) : null;
            return (
              <div key={invite.userId} className="flex items-center justify-between gap-3 rounded-lg border p-3">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 truncate text-sm font-medium">
                    {invite.username}
                    {invite.ssoOnly ? (
                      <Badge variant="secondary" className="text-[10px]">
                        {t("access.pending.ssoOnly")}
                      </Badge>
                    ) : null}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {roles.find((role) => role.id === invite.roleId)?.name ?? invite.roleId} ·{" "}
                    {invite.ssoOnly
                      ? t("access.people.awaitingSignIn")
                      : expiresIn
                        ? t("access.pending.expires", { when: expiresIn })
                        : t("access.people.inviteExpired")}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  {/* An SSO-only account has no link: nothing to resend. */}
                  {invite.ssoOnly ? null : (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-8 text-xs"
                      disabled={mutations.busy}
                      onClick={() =>
                        void mutations.resend
                          .mutateAsync(invite.userId)
                          .then((issued) => onIssued({ invite: issued, username: null }))
                          .catch(() => undefined)
                      }
                    >
                      {t("access.people.actions.copyNewLink")}
                    </Button>
                  )}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-8 text-xs text-destructive hover:text-destructive"
                    disabled={mutations.busy}
                    onClick={() =>
                      void mutations.revoke
                        .mutateAsync(invite.userId)
                        .then(() => toast.success(t("access.people.toasts.inviteRevoked")))
                        .catch(() => undefined)
                    }
                  >
                    {t("access.people.actions.revokeInvite")}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </SheetContent>
    </Sheet>
  );
}

import { MoreHorizontal } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { AlertMessage } from "@/components/alert-message";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { DashboardRole, DashboardUser } from "@/features/access/api";
import type { useAccessMutations } from "@/features/access/hooks";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import type { IssuedLink } from "@/features/settings/components/access/invite-dialog";
import { RoleSelectItems } from "@/features/settings/components/access/role-picker";

// The migrated `admin` account keeps its role and status and cannot be deleted
// in this release (`409 compat_user_locked`); its TOTP can only be reset while
// "require TOTP on login" is off.
const COMPAT_ADMIN_USERNAME = "admin";

type RowDialog = "role" | "delete" | null;
type MenuItem = { key: string; label: string; onSelect: () => void; destructive?: boolean };

export type PeopleRowActionsProps = {
  user: DashboardUser;
  isSelf: boolean;
  /** The configured "require TOTP on login" policy (dashboard settings); `undefined` while unknown. */
  totpPolicyOn: boolean | undefined;
  roles: readonly DashboardRole[];
  assignableRoleIds: readonly string[];
  mutations: ReturnType<typeof useAccessMutations>;
  onIssued: (issued: IssuedLink) => void;
};

/**
 * Per-row menu offering only what the server honours: the self row can only
 * sign itself out everywhere (through the store, a clean client logout), the
 * migrated `admin` row keeps role and status, invited rows get invite actions.
 * Every remaining refusal comes back from the server and is shown by the tab.
 */
export function PeopleRowActions({
  user,
  isSelf,
  totpPolicyOn,
  roles,
  assignableRoleIds,
  mutations,
  onIssued,
}: PeopleRowActionsProps) {
  const { t } = useTranslation();
  const [dialog, setDialog] = useState<RowDialog>(null);
  const [roleId, setRoleId] = useState(user.role.id);
  const name = user.displayName ?? user.username;
  const assignable = roles.filter((role) => assignableRoleIds.includes(role.id));
  const isCompat = user.username === COMPAT_ADMIN_USERNAME;
  // The company login owns this role; changing it pins the account to manual.
  const managedExternally = user.roleSource !== "manual";
  const locked = isSelf || isCompat;

  const run = (promise: Promise<unknown>, successKey: string) =>
    void promise.then(() => toast.success(t(successKey))).catch(() => undefined);
  const setStatus = (status: "active" | "disabled", successKey: string) =>
    run(mutations.updateUser.mutateAsync({ userId: user.id, payload: { status } }), successKey);

  const items: MenuItem[] = [];
  if (user.status === "invited") {
    // An SSO-only account has no link: nothing to resend.
    if (!user.pendingInvite?.ssoOnly) {
      items.push({
        key: "resend",
        label: t("access.people.actions.copyNewLink"),
        onSelect: () =>
          void mutations.resend
            .mutateAsync(user.id)
            .then((invite) => onIssued({ invite, username: null }))
            .catch(() => undefined),
      });
    }
    items.push(
      {
        key: "revoke",
        label: t("access.people.actions.revokeInvite"),
        destructive: true,
        onSelect: () => run(mutations.revoke.mutateAsync(user.id), "access.people.toasts.inviteRevoked"),
      },
    );
  } else {
    if (!locked) {
      items.push({
        key: "role",
        label: managedExternally
          ? t("access.people.actions.takeOverRole")
          : t("access.people.actions.changeRole"),
        onSelect: () => {
          setRoleId(user.role.id);
          setDialog("role");
        },
      });
      items.push(
        user.status === "active"
          ? { key: "disable", label: t("access.people.actions.disable"), onSelect: () => setStatus("disabled", "access.people.toasts.disabled") }
          : { key: "enable", label: t("access.people.actions.enable"), onSelect: () => setStatus("active", "access.people.toasts.enabled") },
      );
    }
    // Unknown policy counts as "on" for the compat row: the server would refuse.
    if (user.totpConfigured && !isSelf && !(isCompat && totpPolicyOn !== false)) {
      items.push({
        key: "reset-totp",
        label: t("access.people.actions.resetTotp"),
        onSelect: () => run(mutations.resetTotp.mutateAsync(user.id), "access.people.toasts.totpReset"),
      });
    }
    items.push({
      key: "revoke-sessions",
      label: t("access.people.actions.logoutEverywhere"),
      onSelect: () =>
        isSelf
          ? void useAuthStore.getState().logoutEverywhere()
          : run(mutations.revokeSessions.mutateAsync(user.id), "access.people.toasts.sessionsRevoked"),
    });
    if (!locked) {
      items.push({ key: "delete", label: t("access.people.actions.delete"), destructive: true, onSelect: () => setDialog("delete") });
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("access.people.actions.menu", { name })}
            disabled={mutations.busy}
          >
            <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {items.map((item, index) => (
            <span key={item.key}>
              {item.destructive && index > 0 ? <DropdownMenuSeparator /> : null}
              <DropdownMenuItem
                onSelect={item.onSelect}
                className={item.destructive ? "text-destructive focus:text-destructive" : undefined}
              >
                {item.label}
              </DropdownMenuItem>
            </span>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={dialog === "role"} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("access.people.changeRole.title")}</DialogTitle>
            <DialogDescription>{t("access.people.changeRole.description", { name })}</DialogDescription>
          </DialogHeader>
          {managedExternally ? (
            <AlertMessage variant="warning">{t("access.people.changeRole.takeOverWarning")}</AlertMessage>
          ) : null}
          <Select value={roleId} onValueChange={setRoleId}>
            <SelectTrigger aria-label={t("access.invite.roleLabel")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <RoleSelectItems roles={assignable} />
            </SelectContent>
          </Select>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDialog(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              disabled={roleId === user.role.id || mutations.busy}
              onClick={() => {
                setDialog(null);
                run(
                  mutations.updateUser.mutateAsync({
                    userId: user.id,
                    // Confirming here IS the take-over the server asks for.
                    payload: managedExternally ? { roleId, force: true } : { roleId },
                  }),
                  "access.people.toasts.roleChanged",
                );
              }}
            >
              {managedExternally
                ? t("access.people.changeRole.takeOverSubmit")
                : t("access.people.changeRole.submit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={dialog === "delete"} onOpenChange={(open) => !open && setDialog(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("access.people.delete.title", { name })}</AlertDialogTitle>
            <AlertDialogDescription>{t("access.people.delete.description")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => run(mutations.deleteUser.mutateAsync(user.id), "access.people.toasts.deleted")}
            >
              {t("access.people.actions.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

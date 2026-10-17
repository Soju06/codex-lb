import { MoreHorizontal } from "lucide-react";
import { useId, useState } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DashboardUserCreateRequestSchema, type DashboardRole, type DashboardUser } from "@/features/access/api";
import type { useAccessMutations } from "@/features/access/hooks";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import type { IssuedLink } from "@/features/settings/components/access/invite-dialog";
import { RoleSelectItems } from "@/features/settings/components/access/role-picker";

// The same rule the invite dialog validates a new name against, so a malformed
// one is refused before the round-trip. Names the server reserves (and names
// already taken) come back as refusals: the client keeps no list of its own.
const UsernameSchema = DashboardUserCreateRequestSchema.shape.username;

type RowDialog = "role" | "rename" | "delete" | null;
type MenuItem = { key: string; label: string; onSelect: () => void; destructive?: boolean };

export type PeopleRowActionsProps = {
  user: DashboardUser;
  isSelf: boolean;
  roles: readonly DashboardRole[];
  assignableRoleIds: readonly string[];
  mutations: ReturnType<typeof useAccessMutations>;
  onIssued: (issued: IssuedLink) => void;
};

/**
 * Per-row menu offering only what the server honours: the self row can only
 * sign itself out everywhere (through the store, a clean client logout) and
 * invited rows get invite actions. No row is treated differently for the
 * account it is -- the account the install bootstrapped included: every
 * remaining refusal comes back from the server and is shown by the tab.
 */
export function PeopleRowActions({ user, isSelf, roles, assignableRoleIds, mutations, onIssued }: PeopleRowActionsProps) {
  const { t } = useTranslation();
  const usernameInputId = useId();
  const [dialog, setDialog] = useState<RowDialog>(null);
  const [roleId, setRoleId] = useState(user.role.id);
  const [username, setUsername] = useState(user.username);
  const [usernameError, setUsernameError] = useState<string | null>(null);
  const name = user.displayName ?? user.username;
  const assignable = roles.filter((role) => assignableRoleIds.includes(role.id));
  // The company login owns this role; changing it pins the account to manual.
  const managedExternally = user.roleSource !== "manual";

  const run = (promise: Promise<unknown>, successKey: string) =>
    void promise.then(() => toast.success(t(successKey))).catch(() => undefined);
  const setStatus = (status: "active" | "disabled", successKey: string) =>
    run(mutations.updateUser.mutateAsync({ userId: user.id, payload: { status } }), successKey);
  const submitRename = () => {
    const parsed = UsernameSchema.safeParse(username);
    if (!parsed.success) {
      setUsernameError(parsed.error.issues[0]?.message ?? "access.invite.validation.usernameRequired");
      return;
    }
    setDialog(null);
    if (parsed.data === user.username) return;
    run(
      mutations.updateUser.mutateAsync({ userId: user.id, payload: { username: parsed.data } }),
      "access.people.toasts.renamed",
    );
  };

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
    if (!isSelf) {
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
      items.push({
        key: "rename",
        label: t("access.people.actions.rename"),
        onSelect: () => {
          setUsername(user.username);
          setUsernameError(null);
          setDialog("rename");
        },
      });
      items.push(
        user.status === "active"
          ? { key: "disable", label: t("access.people.actions.disable"), onSelect: () => setStatus("disabled", "access.people.toasts.disabled") }
          : { key: "enable", label: t("access.people.actions.enable"), onSelect: () => setStatus("active", "access.people.toasts.enabled") },
      );
    }
    if (user.totpConfigured && !isSelf) {
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
    if (!isSelf) {
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

      <Dialog open={dialog === "rename"} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("access.people.rename.title")}</DialogTitle>
            <DialogDescription>{t("access.people.rename.description", { name })}</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor={usernameInputId}>{t("access.people.rename.label")}</Label>
            <Input
              id={usernameInputId}
              value={username}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              onChange={(event) => {
                setUsername(event.target.value);
                setUsernameError(null);
              }}
            />
            {usernameError ? (
              <p role="alert" className="text-xs text-destructive">
                {t(usernameError)}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDialog(null)}>
              {t("common.cancel")}
            </Button>
            <Button type="button" disabled={mutations.busy} onClick={submitRename}>
              {t("access.people.rename.submit")}
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

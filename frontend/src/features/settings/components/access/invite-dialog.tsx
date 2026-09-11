import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  DashboardUserCreateRequestSchema,
  inviteLinkFor,
  type DashboardRole,
  type DashboardUserCreateRequest,
  type IssuedInvite,
} from "@/features/access/api";
import { accessErrorMessage, useAccessMutations, useDashboardRoles } from "@/features/access/hooks";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { ApiError } from "@/lib/api-client";

const DEFAULT_ROLE_SLUG = "operator";

/** A freshly issued link plus who it is for (`null` when it replaced an older link). */
export type IssuedLink = { invite: IssuedInvite; username: string | null };

export type IssuedLinkDialogProps = {
  issued: IssuedLink | null;
  onClose: () => void;
};

/**
 * The one time the invite link is on screen: copy button and the expiry note.
 * Owned by the Access card / page, not by the tier-dependent subtree, so the
 * first invite's tier flip cannot unmount it.
 */
export function IssuedLinkDialog({ issued, onClose }: IssuedLinkDialogProps) {
  const { t } = useTranslation();
  const link = issued ? inviteLinkFor(issued.invite.token) : "";
  return (
    <Dialog open={issued !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("access.invite.link.title")}</DialogTitle>
          <DialogDescription>
            {issued?.username
              ? t("access.invite.link.created", { username: issued.username })
              : t("access.invite.link.resent")}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">{t("access.invite.link.description")}</p>
          <div className="flex min-w-0 items-center gap-2 overflow-hidden rounded-lg border bg-muted/20 px-3 py-2">
            <p className="min-w-0 flex-1 truncate font-mono text-xs" data-testid="invite-link">
              {link}
            </p>
            <CopyButton value={link} label={t("access.invite.link.copy")} />
          </div>
        </div>
        <DialogFooter>
          <Button type="button" onClick={onClose}>
            {t("common.actions.done")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function roleSummary(role: DashboardRole, t: ReturnType<typeof useTranslation>["t"]): string {
  return t(`access.roles.${role.slug}.summary`, { defaultValue: role.description ?? "" });
}

export type InviteDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called once with the new account's link; the caller shows it exactly once. */
  onIssued: (issued: IssuedLink) => void;
};

/**
 * Creates an invited account. The role list is the session's
 * `assignableRoleIds` (presets Admin/Operator/Viewer this release); Operator is
 * preselected, Admin adds a warning, and the very first invite says that the
 * sign-in screen will ask for a username from now on. The session is not
 * refreshed here: the owner does that after the link has been acknowledged.
 */
export function InviteDialog({ open, onOpenChange, onIssued }: InviteDialogProps) {
  const { t } = useTranslation();
  const assignableRoleIds = useAuthStore((state) => state.assignableRoleIds);
  const accessSummary = useAuthStore((state) => state.accessSummary);
  const username = useAuthStore((state) => state.user?.username ?? "admin");
  const rolesQuery = useDashboardRoles(open);
  const [error, setError] = useState<string | null>(null);
  const form = useForm<DashboardUserCreateRequest>({
    resolver: zodResolver(DashboardUserCreateRequestSchema),
    defaultValues: { username: "", displayName: "", roleId: "" },
  });
  const { createUser } = useAccessMutations();

  const roles = (rolesQuery.data ?? []).filter((role) => assignableRoleIds.includes(role.id));
  const defaultRoleId = (roles.find((role) => role.slug === DEFAULT_ROLE_SLUG) ?? roles[0])?.id ?? "";
  const chosenRoleId = useWatch({ control: form.control, name: "roleId" });
  const roleId = chosenRoleId || defaultRoleId;
  const selectedRole = roles.find((role) => role.id === roleId);
  const isFirstInvite = (accessSummary?.usersTotal ?? 1) <= 1 && (accessSummary?.pendingInvites ?? 0) === 0;

  const close = () => {
    onOpenChange(false);
    form.reset();
    setError(null);
  };

  const submit = async (values: DashboardUserCreateRequest) => {
    setError(null);
    try {
      const created = await createUser.mutateAsync({
        username: values.username.toLowerCase(),
        displayName: values.displayName?.trim() ? values.displayName.trim() : undefined,
        roleId: values.roleId || defaultRoleId,
      });
      close();
      onIssued({ invite: created.invite, username: created.user.username });
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "username_taken") {
        form.setError("username", { message: accessErrorMessage(caught, t) });
        return;
      }
      setError(accessErrorMessage(caught, t));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("access.invite.title")}</DialogTitle>
          <DialogDescription>{t("access.invite.description")}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(submit)} className="space-y-4">
            {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
            <FormField
              control={form.control}
              name="username"
              render={({ field, fieldState }) => (
                <FormItem>
                  <FormLabel>{t("access.invite.usernameLabel")}</FormLabel>
                  <FormControl>
                    <Input {...field} autoComplete="off" placeholder={t("access.invite.usernamePlaceholder")} />
                  </FormControl>
                  <FormMessage>{fieldState.error?.message ? t(fieldState.error.message) : null}</FormMessage>
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="displayName"
              render={({ field, fieldState }) => (
                <FormItem>
                  <FormLabel>{t("access.invite.displayNameLabel")}</FormLabel>
                  <FormControl>
                    <Input {...field} value={field.value ?? ""} autoComplete="off" />
                  </FormControl>
                  <FormMessage>{fieldState.error?.message ? t(fieldState.error.message) : null}</FormMessage>
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="roleId"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("access.invite.roleLabel")}</FormLabel>
                  <Select value={roleId} onValueChange={field.onChange} disabled={roles.length === 0}>
                    <FormControl>
                      <SelectTrigger aria-label={t("access.invite.roleLabel")}>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {roles.map((role) => (
                        <SelectItem key={role.id} value={role.id}>
                          {role.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedRole ? <FormDescription>{roleSummary(selectedRole, t)}</FormDescription> : null}
                </FormItem>
              )}
            />
            {selectedRole?.slug === "admin" ? (
              <AlertMessage variant="warning">{t("access.invite.adminWarning")}</AlertMessage>
            ) : null}
            {isFirstInvite ? (
              <p className="text-xs text-muted-foreground" data-testid="first-invite-note">
                {t("access.invite.firstInviteNote", { username })}
              </p>
            ) : null}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={close} disabled={createUser.isPending}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={createUser.isPending || roles.length === 0}>
                {t("access.invite.submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

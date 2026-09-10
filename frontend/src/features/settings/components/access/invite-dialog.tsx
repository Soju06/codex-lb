import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { AlertMessage } from "@/components/alert-message";
import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  DashboardUserCreateRequestSchema,
  inviteLinkFor,
  type DashboardRole,
  type DashboardUserCreateRequest,
  type IssuedInvite,
} from "@/features/access/api";
import { accessErrorMessage, useAccessMutations, useDashboardRoles } from "@/features/access/hooks";
import { RoleSelectItems } from "@/features/settings/components/access/role-picker";
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
 *
 * When a provider other than the password is active (the reverse proxy), the
 * dialog also offers to add the person without a password: the account waits
 * for the identity the proxy sends and there is no link to hand over.
 */
export function InviteDialog({ open, onOpenChange, onIssued }: InviteDialogProps) {
  const { t } = useTranslation();
  const assignableRoleIds = useAuthStore((state) => state.assignableRoleIds);
  const accessSummary = useAuthStore((state) => state.accessSummary);
  const username = useAuthStore((state) => state.user?.username ?? "admin");
  const ssoProvider = useAuthStore((state) => state.loginHint.providers.find((p) => p.kind !== "password") ?? null);
  const rolesQuery = useDashboardRoles(open);
  const [error, setError] = useState<string | null>(null);
  const [ssoOnly, setSsoOnly] = useState(false);
  const [subject, setSubject] = useState("");
  const [subjectError, setSubjectError] = useState<string | null>(null);
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
    setSsoOnly(false);
    setSubject("");
    setSubjectError(null);
  };

  const submit = async (values: DashboardUserCreateRequest) => {
    setError(null);
    setSubjectError(null);
    const useSso = ssoOnly && ssoProvider !== null;
    const trimmedSubject = subject.trim();
    if (useSso && trimmedSubject === "") {
      setSubjectError(t("access.invite.sso.validation.identityRequired"));
      return;
    }
    try {
      const created = await createUser.mutateAsync({
        username: values.username.toLowerCase(),
        displayName: values.displayName?.trim() ? values.displayName.trim() : undefined,
        roleId: values.roleId || defaultRoleId,
        ...(useSso
          ? {
              ssoOnly: true,
              expectedIdentity: {
                provider: ssoProvider.kind,
                providerKey: ssoProvider.providerKey,
                subject: trimmedSubject,
              },
            }
          : {}),
      });
      close();
      if (created.invite === null) {
        // Nothing to hand over: the account activates on the person's first proxy sign-in.
        toast.success(t("access.invite.sso.added", { username: created.user.username }));
        void useAuthStore.getState().refreshSession().catch(() => undefined);
        return;
      }
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
                      <RoleSelectItems roles={roles} />
                    </SelectContent>
                  </Select>
                  {selectedRole ? <FormDescription>{roleSummary(selectedRole, t)}</FormDescription> : null}
                </FormItem>
              )}
            />
            {selectedRole?.slug === "admin" ? (
              <AlertMessage variant="warning">{t("access.invite.adminWarning")}</AlertMessage>
            ) : null}
            {ssoProvider ? (
              <div className="space-y-3 rounded-lg border p-3" data-testid="invite-sso-option">
                <div className="flex items-start gap-2">
                  <Checkbox
                    id="invite-sso-only"
                    checked={ssoOnly}
                    onCheckedChange={(checked) => setSsoOnly(checked === true)}
                  />
                  <div className="grid gap-1">
                    <Label htmlFor="invite-sso-only" className="text-sm font-medium">
                      {t("access.invite.sso.label")}
                    </Label>
                    <p className="text-xs text-muted-foreground">
                      {t("access.invite.sso.description", { provider: ssoProvider.label })}
                    </p>
                  </div>
                </div>
                {ssoOnly ? (
                  <div className="space-y-1">
                    <Label htmlFor="invite-sso-subject">{t("access.invite.sso.identityLabel")}</Label>
                    <Input
                      id="invite-sso-subject"
                      value={subject}
                      autoComplete="off"
                      placeholder={t("access.invite.sso.identityPlaceholder")}
                      onChange={(event) => setSubject(event.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">{t("access.invite.sso.identityHelp")}</p>
                    {subjectError ? <p className="text-xs text-destructive">{subjectError}</p> : null}
                  </div>
                ) : null}
              </div>
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
                {ssoOnly && ssoProvider ? t("access.invite.sso.submit") : t("access.invite.submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

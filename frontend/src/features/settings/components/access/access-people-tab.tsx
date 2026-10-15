import { ShieldCheck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { AlertMessage } from "@/components/alert-message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { DashboardUser } from "@/features/access/api";
import {
  accessErrorMessage,
  useAccessMutations,
  useDashboardRoles,
  useDashboardUsers,
  usePendingInvites,
  usePermissionDescriptors,
} from "@/features/access/hooks";
import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
import { ACCESS_HASH } from "@/features/settings/advanced-settings-deeplink";
import { getSettings, updateSettings } from "@/features/settings/api";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import type { IssuedLink } from "@/features/settings/components/access/invite-dialog";
import { PendingInvitesSheet } from "@/features/settings/components/access/pending-invites-sheet";
import { PeopleRowActions } from "@/features/settings/components/access/people-row-actions";
import { RoleBadge } from "@/features/settings/components/access/role-badge";
import { RolesSheet } from "@/features/settings/components/access/roles-sheet";
import { formatDateTimeInline, formatExpiresIn } from "@/utils/formatters";
import { getErrorMessage } from "@/utils/errors";

// Above this many rows the card offers the full page.
const FULL_PAGE_THRESHOLD = 8;

type Sheet = "pending" | "roles" | null;

export type AccessPeopleTabProps = {
  /** `/settings/access`: no "view full page" link, wider table. */
  fullPage?: boolean;
  onOpenMySignIn?: () => void;
  /** The invite dialog and the one-time link are owned by the card/page. */
  onInvite: () => void;
  onIssued: (issued: IssuedLink) => void;
};

function StatusCell({ user }: { user: DashboardUser }) {
  const { t } = useTranslation();
  if (user.status !== "invited") {
    return (
      <Badge variant={user.status === "active" ? "secondary" : "outline"}>
        {t(`access.people.status.${user.status}`)}
      </Badge>
    );
  }
  const expiresIn = user.pendingInvite?.expiresAt ? formatExpiresIn(user.pendingInvite.expiresAt) : null;
  return (
    <div className="space-y-0.5">
      <Badge variant="outline">{t("access.people.status.invited")}</Badge>
      <p className="text-[11px] text-muted-foreground">
        {user.pendingInvite?.ssoOnly
          ? t("access.people.awaitingSignIn")
          : expiresIn
            ? t("access.people.inviteExpires", { when: expiresIn })
            : t("access.people.inviteExpired")}
      </p>
    </div>
  );
}

/**
 * "Require two-factor for administrators" (D9): the one requirement control the
 * People tab owns, rendered only with `security:write`. The server refuses to
 * turn it on for an actor without their own secret (`invalid_totp_config`); that
 * refusal is worded here, everything else shows the server message.
 */
function AdminTotpRequirement({ settings }: { settings: DashboardSettings }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (enabled: boolean) =>
      updateSettings(buildSettingsUpdateRequest(settings, { totpRequiredForAdminRole: enabled })),
    onMutate: () => setError(null),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] }),
    onError: (caught) => {
      if (caught instanceof ApiError && caught.code === "settings_conflict") {
        // Another writer committed since the settings loaded; refetch so the retry carries the fresh version.
        void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
      }
      setError(
        caught instanceof ApiError && caught.code === "invalid_totp_config"
          ? t("access.people.signInRequirements.adminTotp.setUpYourOwnFirst")
          : getErrorMessage(caught),
      );
    },
  });
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
        <div>
          <p className="text-sm font-medium text-foreground">{t("access.people.signInRequirements.adminTotp.label")}</p>
          <p className="text-xs text-muted-foreground">
            {t("access.people.signInRequirements.adminTotp.description")}
            {settings.adminsWithoutTotpCount > 0
              ? ` ${t("access.people.signInRequirements.adminTotp.pendingEnrolment", { count: settings.adminsWithoutTotpCount })}`
              : null}
          </p>
        </div>
        <Switch
          aria-label={t("access.people.signInRequirements.adminTotp.label")}
          checked={settings.totpRequiredForAdminRole}
          disabled={mutation.isPending}
          onCheckedChange={(checked) => mutation.mutate(checked)}
        />
      </div>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Everyone who can sign in: the table, its row actions and the invite entry points. */
export function AccessPeopleTab({ fullPage = false, onOpenMySignIn, onInvite, onIssued }: AccessPeopleTabProps) {
  const { t } = useTranslation();
  const selfId = useAuthStore((state) => state.user?.id ?? null);
  const assignableRoleIds = useAuthStore((state) => state.assignableRoleIds);
  const canWriteSecurity = usePermission("security:write");
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  const usersQuery = useDashboardUsers();
  const invitesQuery = usePendingInvites();
  const rolesQuery = useDashboardRoles();
  const permissionsQuery = usePermissionDescriptors();
  // The configured policy, not the session's per-login challenge flag (which
  // is false again once this admin has passed TOTP). Shares the Settings
  // page's cache entry and loads it on `/settings/access`. Unknown while it
  // loads or after a failure: the tab then states no policy at all rather than
  // guessing one (fail closed).
  const settingsQuery = useQuery({ queryKey: ["settings", "detail"], queryFn: getSettings });
  const totpPolicyOn: boolean | undefined = settingsQuery.data?.totpRequiredOnLogin;
  const mutations = useAccessMutations({
    onMutate: () => setError(null),
    onError: (caught) => setError(accessErrorMessage(caught, t)),
  });

  const users = usersQuery.data ?? [];
  const roles = rolesQuery.data ?? [];
  const descriptors = permissionsQuery.data ?? [];
  const pendingCount = invitesQuery.data?.length ?? 0;
  const loadError = usersQuery.error ?? rolesQuery.error;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" className="h-8 text-xs" onClick={onInvite}>
          {t("access.people.invite")}
        </Button>
        {pendingCount > 0 ? (
          <Button type="button" size="sm" variant="outline" className="h-8 text-xs" onClick={() => setSheet("pending")}>
            {t("access.people.pendingInvites", { count: pendingCount })}
          </Button>
        ) : null}
        <Button type="button" variant="link" size="sm" className="ml-auto h-8 px-0 text-xs" onClick={() => setSheet("roles")}>
          {t("access.people.viewRoles")}
        </Button>
      </div>

      {error ? (
        <div role="alert">
          <AlertMessage variant="error">{error}</AlertMessage>
        </div>
      ) : null}
      {loadError ? <AlertMessage variant="error">{accessErrorMessage(loadError, t)}</AlertMessage> : null}

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("access.people.columns.name")}</TableHead>
              <TableHead>{t("access.people.columns.role")}</TableHead>
              <TableHead>{t("access.people.columns.status")}</TableHead>
              <TableHead>{t("access.people.columns.lastSignIn")}</TableHead>
              <TableHead>{t("access.people.columns.signInMethod")}</TableHead>
              <TableHead className="w-10">
                <span className="sr-only">{t("access.people.columns.actions")}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {usersQuery.isPending ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-xs text-muted-foreground">
                  {t("common.loading")}
                </TableCell>
              </TableRow>
            ) : null}
            {users.map((user) => {
              const isSelf = user.id === selfId;
              return (
                <TableRow key={user.id} data-testid={`people-row-${user.username}`}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{user.displayName ?? user.username}</p>
                        {user.displayName ? <p className="truncate text-xs text-muted-foreground">{user.username}</p> : null}
                      </div>
                      {isSelf ? <Badge variant="outline">{t("access.people.you")}</Badge> : null}
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex flex-wrap items-center gap-1.5">
                      <RoleBadge role={user.role} roles={roles} descriptors={descriptors} />
                      {/* The company login owns this role until someone takes it over. */}
                      {user.roleSource !== "manual" ? (
                        <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">
                          {t("access.people.managedExternally")}
                        </Badge>
                      ) : null}
                    </span>
                  </TableCell>
                  <TableCell>
                    <StatusCell user={user} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {user.lastLoginAt ? formatDateTimeInline(user.lastLoginAt) : t("common.time.never")}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      {user.hasPassword ? t("access.people.method.password") : "—"}
                      {user.totpConfigured ? (
                        <ShieldCheck
                          className="h-3.5 w-3.5 text-primary"
                          role="img"
                          aria-label={t("access.people.method.totp")}
                        />
                      ) : null}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <PeopleRowActions
                      user={user}
                      isSelf={isSelf}
                      roles={roles}
                      assignableRoleIds={assignableRoleIds}
                      mutations={mutations}
                      onIssued={onIssued}
                    />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {canWriteSecurity && settingsQuery.data ? <AdminTotpRequirement settings={settingsQuery.data} /> : null}

      <div className="flex flex-col gap-2 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
        <p className="flex flex-wrap items-center gap-x-1">
          <span className="font-medium text-foreground">{t("access.people.signInRequirements.label")}</span>{" "}
          {totpPolicyOn === undefined ? (
            settingsQuery.isError ? (
              <span role="alert" className="inline-flex items-center gap-2 text-destructive">
                {t("access.people.signInRequirements.loadFailed")}
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  disabled={settingsQuery.isFetching}
                  onClick={() => void settingsQuery.refetch()}
                >
                  {t("common.actions.retry")}
                </Button>
              </span>
            ) : (
              <span data-testid="sign-in-requirements-loading">{t("common.loading")}</span>
            )
          ) : totpPolicyOn ? (
            t("access.people.signInRequirements.totpOn")
          ) : (
            t("access.people.signInRequirements.totpOff")
          )}{" "}
          {fullPage ? (
            <Link to={`/settings${ACCESS_HASH}`} className="text-primary underline-offset-4 hover:underline">
              {t("access.people.signInRequirements.change")}
            </Link>
          ) : (
            <button type="button" className="text-primary underline-offset-4 hover:underline" onClick={onOpenMySignIn}>
              {t("access.people.signInRequirements.change")}
            </button>
          )}
        </p>
        {!fullPage && users.length > FULL_PAGE_THRESHOLD ? (
          <Link to="/settings/access" className="text-primary underline-offset-4 hover:underline">
            {t("access.people.viewFullPage")}
          </Link>
        ) : null}
      </div>

      <PendingInvitesSheet
        open={sheet === "pending"}
        onOpenChange={(open) => setSheet(open ? "pending" : null)}
        invites={invitesQuery.data ?? []}
        roles={roles}
        onIssued={onIssued}
      />
      <RolesSheet open={sheet === "roles"} onOpenChange={(open) => setSheet(open ? "roles" : null)} />
    </div>
  );
}

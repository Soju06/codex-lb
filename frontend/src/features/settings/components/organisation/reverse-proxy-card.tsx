import { Network } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { AuthProvider } from "@/features/organisation/api";
import { organisationErrorMessage, useOrganisationMutations } from "@/features/organisation/hooks";
import type { PickerRole } from "@/features/organisation/rules";
import { RoleSelectItems } from "@/features/settings/components/access/role-picker";

// Radix `Select` has no empty value, so "nobody" needs a sentinel.
const NO_ROLE = "__none__";

export type ReverseProxyCardProps = {
  provider: AuthProvider;
  roles: readonly PickerRole[];
  mutations: ReturnType<typeof useOrganisationMutations>;
  disabled?: boolean;
};

/**
 * The reverse-proxy sign-in settings. The two header names are deployment
 * topology — they have to match whatever the proxy in front sends — so they
 * are shown, not edited, next to the variables that set them. Everything else
 * on this card is a database row and is editable.
 *
 * The card is deliberately neutral about installs that do not run behind a
 * proxy: an inactive provider is a topology fact, not a misconfiguration.
 */
export function ReverseProxyCard({ provider, roles, mutations, disabled = false }: ReverseProxyCardProps) {
  const { t } = useTranslation();
  const busy = disabled || mutations.updateProvider.isPending;
  const error = mutations.updateProvider.error
    ? organisationErrorMessage(mutations.updateProvider.error, t)
    : null;

  const save = (payload: Parameters<typeof mutations.updateProvider.mutateAsync>[0]["payload"]) =>
    void mutations.updateProvider.mutateAsync({ providerId: provider.id, payload }).catch(() => undefined);

  // D10 ships this on the admin preset: say so once, as advice, never as an error.
  const unknownRole = roles.find((role) => role.id === provider.unknownIdentityRoleId);
  const unknownIsAdmin = unknownRole?.slug === "admin";

  const headers: Array<{ key: string; label: string; value: string | undefined; variable: string }> = [
    {
      key: "identity",
      label: t("organisation.reverseProxy.identityHeader"),
      value: provider.config["identityHeader"],
      variable: "CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER",
    },
    {
      key: "groups",
      label: t("organisation.reverseProxy.groupsHeader"),
      value: provider.config["groupsHeader"],
      variable: "CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER",
    },
  ];

  return (
    <section id="organisation-reverse-proxy" className="scroll-mt-16 space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <Network className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{t("organisation.reverseProxy.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("organisation.reverseProxy.description")}</p>
        </div>
      </div>

      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      {!provider.active ? (
        <p className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-foreground">
          {t("organisation.reverseProxy.inactiveNote")}
        </p>
      ) : null}

      <div className="grid gap-2 rounded-lg border p-3 sm:grid-cols-2">
        {headers.map((header) => (
          <div key={header.key} className="space-y-0.5">
            <p className="text-xs text-muted-foreground">{header.label}</p>
            <p className="font-mono text-sm font-medium">{header.value || "-"}</p>
            <p className="text-[11px] text-muted-foreground">
              {t("organisation.reverseProxy.setVia", { variable: header.variable })}
            </p>
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <div className="space-y-1">
          <Label htmlFor="organisation-unknown-identity" className="text-xs font-medium">
            {t("organisation.reverseProxy.unknownIdentity.label")}
          </Label>
          <Select
            value={provider.unknownIdentityRoleId ?? NO_ROLE}
            disabled={busy || roles.length === 0}
            onValueChange={(value) =>
              save({ unknownIdentityRoleId: value === NO_ROLE ? null : value })
            }
          >
            <SelectTrigger id="organisation-unknown-identity" aria-label={t("organisation.reverseProxy.unknownIdentity.label")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_ROLE}>{t("organisation.reverseProxy.unknownIdentity.refuse")}</SelectItem>
              <RoleSelectItems roles={roles} />
            </SelectContent>
          </Select>
          <p className="text-[11px] text-muted-foreground">
            {t("organisation.reverseProxy.unknownIdentity.help")}
          </p>
          {unknownIsAdmin ? (
            <AlertMessage variant="warning">{t("organisation.reverseProxy.unknownIdentity.adminNote")}</AlertMessage>
          ) : null}
        </div>

        <div className="space-y-1">
          <Label htmlFor="organisation-no-match" className="text-xs font-medium">
            {t("organisation.reverseProxy.noMatch.label")}
          </Label>
          <Select
            value={provider.noMatchRoleId ?? NO_ROLE}
            disabled={busy || roles.length === 0}
            onValueChange={(value) => save({ noMatchRoleId: value === NO_ROLE ? null : value })}
          >
            <SelectTrigger id="organisation-no-match" aria-label={t("organisation.reverseProxy.noMatch.label")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_ROLE}>{t("organisation.reverseProxy.noMatch.turnOff")}</SelectItem>
              <RoleSelectItems roles={roles} />
            </SelectContent>
          </Select>
          <p className="text-[11px] text-muted-foreground">{t("organisation.reverseProxy.noMatch.help")}</p>
        </div>
      </div>

      <div className="flex items-start justify-between gap-3 rounded-lg border p-3">
        <div>
          <p className="text-sm font-medium">{t("organisation.reverseProxy.linkByEmail.label")}</p>
          <p className="text-xs text-muted-foreground">{t("organisation.reverseProxy.linkByEmail.description")}</p>
        </div>
        <Switch
          aria-label={t("organisation.reverseProxy.linkByEmail.label")}
          checked={provider.linkByEmail}
          disabled={busy}
          onCheckedChange={(checked) => save({ linkByEmail: checked })}
        />
      </div>

      <div className="flex items-start justify-between gap-3 rounded-lg border p-3">
        <div>
          <p className="text-sm font-medium">{t("organisation.reverseProxy.skipRoleSync.label")}</p>
          <p className="text-xs text-muted-foreground">{t("organisation.reverseProxy.skipRoleSync.description")}</p>
        </div>
        <Switch
          aria-label={t("organisation.reverseProxy.skipRoleSync.label")}
          checked={provider.skipRoleSync}
          disabled={busy}
          onCheckedChange={(checked) => save({ skipRoleSync: checked })}
        />
      </div>
    </section>
  );
}

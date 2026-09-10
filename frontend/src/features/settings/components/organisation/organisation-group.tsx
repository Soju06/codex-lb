import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";

import { SpinnerBlock } from "@/components/ui/spinner";
import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
import {
  useAssignableRoles,
  useAuthProviders,
  useOrganisationMutations,
  useRoleMappings,
} from "@/features/organisation/hooks";
import { isOrganisationConfigured, rulesOf, trustedHeaderProvider } from "@/features/organisation/rules";
import {
  ORGANISATION_GROUP_ID,
  ORGANISATION_REFUSED_HASH,
  shouldExpandOrganisationSettings,
} from "@/features/settings/advanced-settings-deeplink";
import {
  AdvancedSettingsGroup,
  type SettingsGroupLabelKeys,
} from "@/features/settings/components/advanced-settings-group";
import { GroupRulesCard } from "@/features/settings/components/organisation/group-rules-card";
import { ReverseProxyCard } from "@/features/settings/components/organisation/reverse-proxy-card";

const LABEL_KEYS = {
  show: "organisation.group.show",
  hide: "organisation.group.hide",
  title: "organisation.group.title",
} as const;

const UNCONFIGURED_LABELS: SettingsGroupLabelKeys = { ...LABEL_KEYS, description: "organisation.group.oneLiner" };
const CONFIGURED_LABELS: SettingsGroupLabelKeys = { ...LABEL_KEYS, description: "organisation.group.summary" };

/** Everything the group's cards need, fetched only once the group is open. */
function OrganisationGroupBody({ refusedOpen, disabled }: { refusedOpen: boolean; disabled: boolean }) {
  const { t } = useTranslation();
  const canReadAudit = usePermission("audit:read");
  const providersQuery = useAuthProviders();
  const mappingsQuery = useRoleMappings();
  // The roles the caller may hand out, from the rules API and not the
  // `users:manage` list: this group belongs to `security:write`, and the
  // server has already applied the delegation rule its writes apply.
  const rolesQuery = useAssignableRoles();
  const mutations = useOrganisationMutations();

  if (providersQuery.isLoading || mappingsQuery.isLoading || rolesQuery.isLoading) {
    return <SpinnerBlock />;
  }

  const provider = trustedHeaderProvider(providersQuery.data);
  if (provider === null) {
    return <p className="text-xs text-muted-foreground">{t("organisation.group.noProvider")}</p>;
  }
  const roles = rolesQuery.data ?? [];

  return (
    <>
      <ReverseProxyCard provider={provider} roles={roles} mutations={mutations} disabled={disabled} />
      <GroupRulesCard
        provider={provider}
        roles={roles}
        rules={rulesOf(mappingsQuery.data, provider)}
        mutations={mutations}
        canReadAudit={canReadAudit}
        refusedOpen={refusedOpen}
        disabled={disabled}
      />
    </>
  );
}

/**
 * The second collapsed group at the bottom of Settings, for the things a
 * company install needs and a single-person install never meets. Collapsed it
 * is one line and costs nothing: its children — and therefore every request
 * they make — only exist once it is open.
 *
 * The line itself is deliberately plain until something has been set up; the
 * summary that replaces it counts what exists, using facts the session already
 * carries rather than a request of its own.
 */
export function OrganisationSettingsGroup({ disabled = false }: { disabled?: boolean }) {
  const { hash } = useLocation();
  const canWriteSecurity = usePermission("security:write");
  const accessSummary = useAuthStore((state) => state.accessSummary);

  if (!canWriteSecurity) {
    return null;
  }

  const configured = isOrganisationConfigured(accessSummary);
  const expand = shouldExpandOrganisationSettings(hash);

  return (
    <div id={ORGANISATION_GROUP_ID} className="scroll-mt-16">
      <AdvancedSettingsGroup
        key={expand ? `open:${hash}` : "closed"}
        defaultOpen={expand}
        scrollToId={expand ? ORGANISATION_GROUP_ID : undefined}
        labels={configured ? CONFIGURED_LABELS : UNCONFIGURED_LABELS}
        descriptionValues={{ count: accessSummary?.roleMappings ?? 0 }}
        descriptionTestId="organisation-group-line"
      >
        <OrganisationGroupBody refusedOpen={hash === ORGANISATION_REFUSED_HASH} disabled={disabled} />
      </AdvancedSettingsGroup>
    </div>
  );
}

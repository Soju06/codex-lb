import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";

import { SpinnerBlock } from "@/components/ui/spinner";
import { useDashboardUsers } from "@/features/access/hooks";
import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
import {
  ASSIGNABLE_ROLES_QUERY_KEY,
  MAPPINGS_QUERY_KEY,
  PROVIDERS_QUERY_KEY,
  useAssignableRoles,
  useAuthProviders,
  useOrganisationMutations,
  useOrganisationSettings,
  useRoleMappings,
  useScimTokens,
} from "@/features/organisation/hooks";
import {
  companyLoginLabel,
  hasCompanyLogin,
  hasScimTokens,
  isLocalLoginRestricted,
  isOrganisationConfigured,
  oidcProvider,
  rulesOf,
  trustedHeaderProvider,
} from "@/features/organisation/rules";
import {
  ORGANISATION_GROUP_ID,
  ORGANISATION_LOGIN_POLICY_HASH,
  ORGANISATION_LOGIN_POLICY_ID,
  ORGANISATION_OIDC_HASH,
  ORGANISATION_OIDC_ID,
  ORGANISATION_REFUSED_HASH,
  ORGANISATION_SCIM_HASH,
  ORGANISATION_SCIM_ID,
  shouldExpandOrganisationSettings,
} from "@/features/settings/advanced-settings-deeplink";
import {
  AdvancedSettingsGroup,
  type SettingsGroupLabelKeys,
} from "@/features/settings/components/advanced-settings-group";
import { AutomaticAccountsCard } from "@/features/settings/components/organisation/automatic-accounts-card";
import { GroupRulesCard } from "@/features/settings/components/organisation/group-rules-card";
import { LoginPolicyCard } from "@/features/settings/components/organisation/login-policy-card";
import { OidcCard } from "@/features/settings/components/organisation/oidc-card";
import { ReverseProxyCard } from "@/features/settings/components/organisation/reverse-proxy-card";

const LABEL_KEYS = {
  show: "organisation.group.show",
  hide: "organisation.group.hide",
  title: "organisation.group.title",
} as const;

const UNCONFIGURED_LABELS: SettingsGroupLabelKeys = { ...LABEL_KEYS, description: "organisation.group.oneLiner" };
const CONFIGURED_LABELS: SettingsGroupLabelKeys = { ...LABEL_KEYS, description: "organisation.group.summary" };
// A tightened login policy is configured state of its own: an install can have
// it with no company login at all, and then the count sentence would be a lie.
const RESTRICTED_LABELS: SettingsGroupLabelKeys = {
  ...LABEL_KEYS,
  description: "organisation.group.summaryRestricted",
};
const POLICY_ONLY_LABELS: SettingsGroupLabelKeys = {
  ...LABEL_KEYS,
  description: "organisation.group.summaryPolicyOnly",
};
// A credential for automatic account management outlives the company sign-in
// it was issued against — an operator can switch the provider back off. Saying
// "password sign-in is restricted" for that install would be a plain lie, so
// the fact that survived gets its own sentence.
const AUTOMATIC_ONLY_LABELS: SettingsGroupLabelKeys = {
  ...LABEL_KEYS,
  description: "organisation.group.summaryAutomatic",
};
const AUTOMATIC_RESTRICTED_LABELS: SettingsGroupLabelKeys = {
  ...LABEL_KEYS,
  description: "organisation.group.summaryAutomaticRestricted",
};
// With exactly one company sign-in active the summary names it, from the login
// hint every session already carries. `access_summary` lists provider kinds and
// never labels, and a collapsed group may not ask.
const NAMED_LABELS: SettingsGroupLabelKeys = { ...LABEL_KEYS, description: "organisation.group.summaryNamed" };
const NAMED_RESTRICTED_LABELS: SettingsGroupLabelKeys = {
  ...LABEL_KEYS,
  description: "organisation.group.summaryNamedRestricted",
};

function labelsFor({
  configured,
  companyLogin,
  automatic,
  restricted,
  named,
}: {
  configured: boolean;
  companyLogin: boolean;
  automatic: boolean;
  restricted: boolean;
  named: boolean;
}): SettingsGroupLabelKeys {
  if (!configured) {
    return UNCONFIGURED_LABELS;
  }
  if (!companyLogin) {
    if (automatic) {
      return restricted ? AUTOMATIC_RESTRICTED_LABELS : AUTOMATIC_ONLY_LABELS;
    }
    return POLICY_ONLY_LABELS;
  }
  if (named) {
    return restricted ? NAMED_RESTRICTED_LABELS : NAMED_LABELS;
  }
  return restricted ? RESTRICTED_LABELS : CONFIGURED_LABELS;
}

// The three queries `OrganisationGroupBody` holds its spinner for. The group
// scrolls to its target on one animation frame and never retries, and
// `#organisation-login-policy` is an id *inside* that spinner — so on a cold
// deep link the lookup would find nothing and the page would stay where it
// was. `#organisation` itself is on the wrapper below and exists either way;
// waiting costs it a frame and keeps one rule for both anchors.
const ORGANISATION_LAYOUT_QUERY_KEYS = [
  PROVIDERS_QUERY_KEY,
  MAPPINGS_QUERY_KEY,
  ASSIGNABLE_ROLES_QUERY_KEY,
] as const;

/** Hashes that name a card rather than the group; anything else lands on the group. */
const CARD_ANCHORS: Record<string, string | undefined> = {
  [ORGANISATION_LOGIN_POLICY_HASH]: ORGANISATION_LOGIN_POLICY_ID,
  [ORGANISATION_OIDC_HASH]: ORGANISATION_OIDC_ID,
  [ORGANISATION_SCIM_HASH]: ORGANISATION_SCIM_ID,
};

/** Everything the group's cards need, fetched only once the group is open. */
function OrganisationGroupBody({ refusedOpen, disabled }: { refusedOpen: boolean; disabled: boolean }) {
  const { t } = useTranslation();
  // The people list is `users:manage`, a different permission from the one that
  // gates this group. Without it the policy card cannot name the emergency
  // account; the roles come from the rules API instead, which this group holds.
  const canManageUsers = usePermission("users:manage");
  const canReadAudit = usePermission("audit:read");
  const providersQuery = useAuthProviders();
  const mappingsQuery = useRoleMappings();
  // The roles the caller may hand out, from the rules API and not the
  // `users:manage` list: this group belongs to `security:write`, and the
  // server has already applied the delegation rule its writes apply.
  const rolesQuery = useAssignableRoles();
  const usersQuery = useDashboardUsers(canManageUsers);
  const settingsQuery = useOrganisationSettings();
  const tokensQuery = useScimTokens();
  const mutations = useOrganisationMutations();

  if (providersQuery.isLoading || mappingsQuery.isLoading || rolesQuery.isLoading) {
    return <SpinnerBlock />;
  }

  const provider = trustedHeaderProvider(providersQuery.data);
  const oidc = oidcProvider(providersQuery.data);
  const roles = rolesQuery.data ?? [];
  // The login-policy card describes the local sign-in every install has, so it
  // renders even on an install with nothing else in this group: that install's
  // operator is exactly the one who needs the emergency URL.
  // The queries go in whole, not their `data`: the card is the only thing that
  // can say "this could not be loaded" in the right place, and `undefined` on
  // its own cannot tell a pending request from a failed one.
  const loginPolicyCard = (
    <LoginPolicyCard
      settingsQuery={settingsQuery}
      usersQuery={usersQuery}
      canSeeAccounts={canManageUsers}
      mutations={mutations}
      disabled={disabled}
    />
  );

  // Order per PLAN §4.8: company sign-in, reverse proxy, rules, login policy,
  // automatic account management. The first, the fourth and the last render on
  // every install — every install can connect an identity provider, every
  // install has a local sign-in, and the collapsed line promises automatic
  // account management to all of them — while the proxy cards describe a
  // deployment that may simply not exist. Saying so is a neutral fact about the
  // topology, never a missing piece.
  return (
    <>
      {oidc === null ? null : <OidcCard provider={oidc} mutations={mutations} disabled={disabled} />}
      {provider === null ? (
        <p className="text-xs text-muted-foreground">{t("organisation.group.noReverseProxy")}</p>
      ) : (
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
      )}
      {loginPolicyCard}
      <AutomaticAccountsCard
        tokensQuery={tokensQuery}
        providers={providersQuery.data}
        mutations={mutations}
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
  const { search, hash } = useLocation();
  const canWriteSecurity = usePermission("security:write");
  const accessSummary = useAuthStore((state) => state.accessSummary);
  const providers = useAuthStore((state) => state.loginHint.providers);

  if (!canWriteSecurity) {
    return null;
  }

  // The operator's own word for their identity provider, quoted verbatim: the
  // banned-word rule governs the copy this product writes, not a customer's
  // name for their own system.
  const providerLabel = companyLoginLabel(providers);
  const labels = labelsFor({
    configured: isOrganisationConfigured(accessSummary),
    companyLogin: hasCompanyLogin(accessSummary),
    automatic: hasScimTokens(accessSummary),
    restricted: isLocalLoginRestricted(accessSummary),
    named: providerLabel !== null,
  });
  const expand = shouldExpandOrganisationSettings(search, hash);

  return (
    <div id={ORGANISATION_GROUP_ID} className="scroll-mt-16">
      <AdvancedSettingsGroup
        key={expand ? `open:${hash}` : "closed"}
        defaultOpen={expand}
        scrollToId={expand ? CARD_ANCHORS[hash] ?? ORGANISATION_GROUP_ID : undefined}
        waitForQueryKeys={ORGANISATION_LAYOUT_QUERY_KEYS}
        labels={labels}
        descriptionValues={{ count: accessSummary?.roleMappings ?? 0, provider: providerLabel ?? "" }}
        descriptionTestId="organisation-group-line"
      >
        <OrganisationGroupBody refusedOpen={hash === ORGANISATION_REFUSED_HASH} disabled={disabled} />
      </AdvancedSettingsGroup>
    </div>
  );
}

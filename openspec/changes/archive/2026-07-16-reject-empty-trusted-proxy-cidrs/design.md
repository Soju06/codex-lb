## Context

The trusted-proxy CIDR field is normalized before validation: empty strings, whitespace-only entries, comma-only input, and an empty list all become `[]`. Individual CIDRs are validated, but the relationship between `firewall_trust_proxy_headers` and the normalized list is enforced only when `dashboard_auth_mode=trusted_header`. Standard and disabled dashboard modes therefore accept enabled header trust with no authorized proxy source.

The remote-deployment documentation always configures the trust flag and CIDR list together. PR #84 introduced source-CIDR-gated forwarded identity, and the later dashboard proxy-auth work added the same non-empty invariant only for `trusted_header` mode. The invariant belongs to proxy trust itself, not to one dashboard mode.

## Goals / Non-Goals

**Goals:**

- Reject enabled proxy-header trust when normalization yields no trusted source CIDRs.
- Apply the invariant across dashboard authentication modes.
- Preserve empty CIDR input as valid when proxy-header trust is disabled.
- Emit an actionable validation error at startup.

**Non-Goals:**

- Change CIDR parsing, canonicalization, or default values.
- Infer a CIDR from the runtime socket or container network.
- Change forwarded-header resolution or firewall allowlist behavior.

## Decisions

### Validate the normalized cross-field state

Add a model-level settings validator after field normalization. It will reject `firewall_trust_proxy_headers=true` when `firewall_trusted_proxy_cidrs` is empty. This catches every textual representation that normalizes to an empty list and keeps individual CIDR syntax validation in the existing field validator.

Alternative: reject an empty raw environment string in the field validator. Rejected because field validation cannot distinguish whether header trust is enabled and would also forbid an intentionally empty list while trust is disabled.

### Fail startup instead of changing operator intent

Do not silently disable header trust and do not repopulate default loopback CIDRs. Both alternatives would run with a configuration different from the explicit input. Startup rejection exposes the contradiction and tells the operator to configure a CIDR or turn trust off.

Alternative: keep accepting the configuration as a fail-closed staging state. Rejected because enabled trust has no authorized source, silently alters local/proxied classification, and contradicts the documented two-setting contract.

### Remove the dashboard-mode duplicate

The existing `trusted_header` validator will continue to require proxy-header trust. Its narrower non-empty-CIDR check becomes redundant under the shared invariant and will be removed so one validator owns the relationship.

## Risks / Trade-offs

- [A deployment intentionally stages the trust flag before its CIDRs] → Startup now fails; stage both settings atomically or leave trust disabled.
- [Empty CIDRs with trust disabled regress] → Cover this valid boundary explicitly.
- [Dashboard trusted-header error text changes for empty CIDRs] → The replacement error names the underlying proxy-trust settings and gives both valid corrections.

## Migration Plan

Before upgrading, any deployment with `CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS=true` and an empty `CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS` must either configure the actual proxy CIDR or set header trust to false. No data migration is required. Rollback restores acceptance of the contradictory configuration.
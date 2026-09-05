# Guest audit-log access context

## Decision

Apply the existing `require_dashboard_admin_access` dependency to the audit
router. It rejects before audit context/service resolution and uses the stable
`admin_access_required` envelope.

## Constraints

- Do not design a partially redacted guest schema; arbitrary `details` requires
  an independently specified allowlist.
- Preserve raw actor IP, details, and request ID for admins.
- No persistence, retention, filtering, setting, or frontend change.

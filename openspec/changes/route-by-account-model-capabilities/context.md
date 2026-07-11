# Account capability routing context

## Three-state capability model

For an active account, a requested capability is one of:

- **supported**: its current or retained last-known catalog advertised it;
- **unsupported**: its authoritative catalog omitted it;
- **unknown**: no successful catalog is available for that account.

Only a complete active-account snapshot may turn catalog omission into an
authoritative routing exclusion. If an account is unknown, selection falls
back to the existing plan-level rules for the whole request rather than
silently treating the account as unsupported. This degradation path favors
availability during startup or transient catalog failures and may allow an
upstream model rejection until catalog coverage becomes complete.

Operator-provided model mappings remain outside this discovery contract. An
unknown mapped slug is not rejected merely because it is absent from the live
subscription catalog.


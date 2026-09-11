# API Key Local Usage Share

Add an optional per-key percentage that limits a scoped API key to its share of each assigned account's local, successful API-key request-log cost budget. This prevents one client key from consuming an assigned account without introducing an account-wide operator cap. Plan capacities are abstract credits and request logs are USD, so the policy estimates a same-unit local USD budget from the account's live upstream used percentage and all local API-key costs in that upstream window; it never compares USD directly to credits.

## Scope

- Persist and expose `account_usage_percent` on API keys.
- Use successful `(account_id, api_key_id)` request-log cost over the account's secondary quota window for admission.
- Keep routing permissive when the snapshot, plan capacity, attribution, cost-to-credit observation, or aggregate query is unavailable.
- Document that request-log persistence is asynchronous, so this is an eventually consistent ledger rather than a cross-request atomic reservation.

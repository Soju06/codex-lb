# Change: retier-topology-settings

## Why

The `MIGRATING` registry in `app/core/config/tiers.py` is the backlog of T3 settings that still live only in the environment. The 2026-09-08 triage of its 50 entries (slop-removal campaign, PLAN §3 M0 and §4) found four rows that do not belong there:

- Three fields were declared T3 but answer "yes" to the tier question "may the value legitimately differ between two replicas?": `proxy_unauthenticated_client_cidrs` (raw socket-peer CIDRs of the replica's network namespace, the sibling of `firewall_trusted_proxy_cidrs` / `forwarded_allow_ips`, not of the projected-IP firewall allowlist its `MIGRATING` row pointed at), `dashboard_trust_loopback_host_header_for_long_sessions` (the last link of the `dashboard_auth_mode` trust chain, decided by how the deployment's reverse proxy rewrites `Host`), and `proxy_response_create_limit` (capacity of a per-process `asyncio.Semaphore`, the same class as `bulkhead_proxy_limit`). Moving any of them to the dashboard would either widen the unauthenticated range on every replica at once without a restart or size a process-local pool from a shared row.
- `telemetry_enabled` already has its dashboard home. Since `telemetry` change `add-telemetry-optout-signal` (#2186) consent resolves `persisted decision > CODEX_LB_TELEMETRY_ENABLED > default`; the environment variable is a fallback that applies only while no decision is saved. The `MIGRATING` row still described the environment as a seed, `docs/configuration.md` still said the variable "overrides the persisted telemetry consent", and the precedence requirement in this capability still listed the telemetry kill switch as an inversion awaiting amendment. The checker could not retire the row because it only recognises a `dashboard_settings` column of the same name, and the home is `dashboard_settings.telemetry_consent`.

## What Changes

- `SETTING_TIERS`: the three topology-bound fields move from T3 to T1; their `MIGRATING` rows are deleted. No `Settings` field is added, removed or renamed; no default or runtime behaviour changes. `docs/reference/settings.md` regenerated (Tier column only).
- New registry `DASHBOARD_HOMES` in `app/core/config/tiers.py`: a T3 field whose database home exists under a different column name (or in another configuration table) maps to it as `table.column`. `scripts/check_settings_tiers.py` accepts such a field as homed, fails when the target is not `table.column` or names a column that does not exist in the SQLAlchemy metadata, and warns when the entry is redundant (same-name column exists, field is not T3 or is gone) or when the same field is also listed in `MIGRATING`. `telemetry_enabled → dashboard_settings.telemetry_consent` is its first entry and leaves `MIGRATING`.
- Wording: the precedence requirement no longer lists the telemetry environment variable as a pending inversion (only the `rate-limit-reset-credits` gate remains); `docs/configuration.md` describes `CODEX_LB_TELEMETRY_ENABLED` as a fallback until a dashboard decision is saved and points the remaining moves at the `MIGRATING` registry.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `configuration-tiers`: MODIFIED "Every setting declares a tier" and "T3 settings have a database home" — a T3 field may satisfy the database-home rule through an explicit `DASHBOARD_HOMES` `table.column` mapping verified by the check; MODIFIED "Precedence is code default, then environment, then dashboard" — the `telemetry` specification already mandates this precedence, so only the reset-credits gate remains a tracked inversion.

## Impact

- Code: `app/core/config/tiers.py`, `app/core/config/settings.py` (tier comments only), `scripts/check_settings_tiers.py`, `tests/unit/test_settings_tiers.py`.
- Docs: `docs/configuration.md`, `docs/reference/settings.md` (generated).
- Operators: none. Every variable keeps its name, type, default and effect.

Part of the slop-removal campaign 0908 (MIGRATING triage, PR T + M0).

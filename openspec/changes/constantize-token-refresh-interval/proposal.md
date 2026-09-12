## Why

`token_refresh_interval_days` is the last entry of the `MIGRATING` backlog in
`app/core/config/tiers.py`. It has never been tuned by any deployment: the
definition line is unchanged since it was introduced, the Helm chart does not
template it, it appears in no `.env.example`, operator doc, issue or incident
note, and neither production env file sets it. `configuration-tiers` requires
every T3 setting to move to the dashboard, but a card for a value nobody flips
is slop (PRINCIPLES.md P2, issue #1340: "a setting the operator never needs to
touch is a default in disguise"). It is also not a recovery lever: an account
is refreshed on demand whenever upstream answers 401, whatever the proactive
window says, so shortening it only adds exchanges and lengthening it only
defers one that has to happen anyway.

`constantize-core-tunables` planned it as the 28th field of that batch and had
to keep it, because it had exactly one live consumer:
`scripts/traffic_analysis/fast_canary_suite.py` injected
`CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS=365` into the controlled failure-matrix
subprocess. That pin is load-bearing. Both controlled runners import the
operator's isolated `auth.json` into a throwaway database and drive real client
turns through the proxy; that file's `last_refresh` is copied verbatim onto the
imported account (`AccountsService.import_account`), and it goes stale within
days because the file is written once and then reused for every run. Without
suppression the first proxied turn calls `should_refresh` -> true and exchanges
the real, single-use refresh token against `https://auth.openai.com`
(`AUTH_BASE_URL` is a protocol constant, so pointing
`CODEX_LB_UPSTREAM_BASE_URL` at the local fixture does not cover OAuth). The
rotated credential is written into a database the suite deletes during cleanup,
so every later canary run starts from a dead file and the harness rots
silently — the exact failure the previous change refused to cause. Dropping the
pin without a replacement is therefore not an option: measured on this host the
isolated `auth.json` is three weeks past the eight-day window, so refresh is
certain, not merely possible.

## What Changes

- The canary's suppression seam moves from "widen the window for the whole
  process" to "the credential was just refreshed".
  `fast_canary_suite.stamp_isolated_auth_refresh` rewrites only the recorded
  refresh time of the validated isolated `auth.json` — every key the account
  importer accepts for it (`lastRefreshAt`, `last_refresh`), so no stale alias
  outranks the stamp — to the current instant (atomic, mode 600 preserved,
  tokens neither read nor logged) before either runner starts. A run lasts minutes and the window is days, so the
  whole run is inside it whatever the constant's value is. The env injection
  and its test assertion are deleted.
  - This is strictly stronger than the pin it replaces: the pin only ever
    reached the failure-matrix subprocess, while the raw HTTP/2 runner depended
    on a `CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS=365` line inside the host-local
    script. One repository-owned preflight now covers both runners, and both
    host-local lines become inert (they produce the removed-setting WARN until
    an operator deletes them).
  - It is also narrower: `365` masked a genuinely stale credential for every
    account in that process; the stamp is scoped to the one isolated identity
    the canary owns, and a run that somehow outlived the fixed interval would
    still refresh.
- **BREAKING** (a documented env var disappears; `extra="ignore"` means no
  deployment fails to start): the `Settings` field, its `SETTING_TIERS` row and
  its `MIGRATING` row are removed. `TOKEN_REFRESH_INTERVAL_DAYS` in
  `app/core/auth/refresh.py` becomes `Final[int] = 8` — the previous default —
  and `should_refresh` reads it directly instead of
  `get_settings().token_refresh_interval_days or <constant>`.
  `CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS` joins `_REMOVED_SETTINGS` for its
  one-release startup WARN.
- The constant stays injectable: `should_refresh` resolves the module attribute
  at call time and `test_should_refresh_reads_the_module_constant_at_call_time`
  monkeypatches it in both directions.
- `MIGRATING` is now empty. `scripts/check_settings_tiers.py` already handles an
  empty mapping (it only iterates it); the docstring, the `tiers.py` comment and
  `docs/configuration.md` are corrected to stop claiming a non-empty backlog,
  and a live-tree test pins that an empty registry passes while a new env-only
  T3 field is still rejected.
- Generated settings reference regenerated (96 -> 95 settings);
  `[settings_fields].max` 96 -> 95; the Helm chart README's 1.24 -> 1.25
  "Removed environment variables" register gains the row.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: MODIFIED "Removed tunables are fixed constants,
  derived values, or dashboard settings" — the proactive token-refresh window
  joins the fixed list.
- `compatibility-tooling`: MODIFIED "Version-aware traffic canary runs without
  false success" — the suite must neutralise proactive token refresh for the
  controlled run through the isolated credential rather than through process
  configuration, with a new scenario.
- `configuration-tiers`: MODIFIED "T3 settings have a database home" — an empty
  `MIGRATING` is the backlog's terminal state and is not a failure, with a new
  scenario.

## Impact

- Code: `app/core/auth/refresh.py`, `app/core/config/settings.py`,
  `app/core/config/tiers.py`, `scripts/check_settings_tiers.py` (docstring),
  `scripts/traffic_analysis/fast_canary_suite.py`.
- No schema change, no API change, no default change. An operator who still
  sets `CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS` gets one startup WARN and keeps
  the previous default behaviour.
- Operators of the traffic-parity canary: the suite now writes to the isolated
  `auth.json` it was already given (one metadata field). The host-local runner
  scripts need no change; their own `CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS=365`
  lines can be deleted at leisure.

Part of the slop-removal campaign 0908 (MIGRATING triage; finishes the
`constantize-core-tunables` batch, which deferred this one field).

## Why

HTTP-bridge reconnect already resolves a live file pin, `require_preferred_account`,
and account-neutral recovery into `required_preferred_account_id`, but it only
marks account-neutral recovery as continuity-owner provenance. Selection then
treats a required file-pin or require-preferred owner as an ordinary preferred
account, so a miss is not typed `continuity_owner_unavailable` and the early
required-owner mapping never fires.

## What Changes

- Pass `preferred_account_is_continuity_owner` from `_reconnect_http_bridge_session`
  whenever `required_preferred_account_id` is set.
- Map typed `continuity_owner_unavailable` to the existing required-owner
  unavailable envelope whenever that required reconnect owner exists, not only
  for account-neutral recovery.
- Keep movable soft `1011` reconnect without a required owner untyped so it
  can still skip the closed account.
- Pin the existing soft-`1011` reconnect tests to that provenance split.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: HTTP-bridge reconnect must type a required owner as
  continuity provenance and map a typed owner miss immediately.
- `sticky-session-operations`: required reconnect owners (file-pin,
  require-preferred, account-neutral) MUST use continuity-owner selection
  provenance; movable soft reconnect MUST NOT.

## Impact

- `app/modules/proxy/_service/http_bridge/mixin.py` reconnect selection kwargs
  and early typed-unavailable mapping.
- Existing unit coverage next to the soft-`1011` reconnect tests.
- No API, schema, dashboard, settings, create-path, affinity, or sticky-write
  changes.

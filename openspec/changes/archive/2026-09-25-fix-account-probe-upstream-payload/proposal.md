# Proposal

## Why

Force Probe returns `probeStatusCode: 400` for a usable Codex account because its direct upstream request includes `max_output_tokens`. The Codex Responses endpoint rejects that field, so operators cannot use the probe to check account health.

## What Changes

- Omit `max_output_tokens` from the fixed Force Probe request body.
- Keep the small prompt, one upstream request, immediate usage refresh, and existing probe response fields.
- Cover the actual dashboard probe route and its outbound request body with a regression test.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Change the Force Probe request contract to exclude the unsupported output-token field.

## Impact

The change affects `app/modules/accounts/service.py`, probe tests, and the usage-refresh-policy spec and context. It does not change account data, proxy routing, or the dashboard response schema.

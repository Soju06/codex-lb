## Why

Issue #546 reports that setting an API key's `enforced_service_tier` to `default` (or `auto`) breaks every request through that key: the request fails upstream with `invalid_request_error Unsupported service_tier: default` (or `... auto`).

The root cause is in `apply_api_key_enforcement` in `app/modules/proxy/request_policy.py`. The enforcement step writes the literal `enforced_service_tier` value onto `payload.service_tier`, and then the request is forwarded as-is. The ChatGPT/Codex backend rejects both `default` and `auto` as literal values: they are codex-lb's API-key surface conventions for "let upstream pick", not upstream-level service tiers.

Today operators have no working way to enforce "no priority / no flex" — exactly the use case the issue raises ("I want to disable fast mode globally or per-key"). The API-key form accepts the value, but every request through that key fails.

## What Changes

- In `apply_api_key_enforcement`, when `enforced_service_tier` is `auto` or `default`, set `payload.service_tier = None` (wire-level absence) instead of writing the literal. Real upstream tiers (`priority`, `flex`) continue to propagate as the literal value.
- Extend the existing `api_key_service_tier_enforced` log line with an `outbound_service_tier` field so operators can confirm enforcement reached upstream as expected.
- Add unit coverage in `tests/unit/test_proxy_utils.py` for the `default` and `auto` omission paths and for the unchanged `flex`/`priority` propagation.

## Capabilities

### Modified Capabilities

- `api-keys`: when the persisted `enforced_service_tier` is `auto` or `default`, the proxy MUST forward the request without a `service_tier` field rather than as a literal value the upstream rejects. Enforcement of `priority` and `flex` is unchanged.

## Impact

- **Code**: `app/modules/proxy/request_policy.py` only.
- **Tests**: `tests/unit/test_proxy_utils.py` (additions, no modifications to existing assertions).
- **API surface**: no schema changes. Existing `enforced_service_tier` validator already accepts `auto`/`default`/`priority`/`flex` (plus `fast` aliased to `priority`); only the outbound mapping changes for the two "let upstream pick" sentinels.
- **Operational**: an extra `outbound_service_tier=...` field on the existing enforcement log; no new env vars or settings.

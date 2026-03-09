## Why

Codex CLI remote compact tasks can run longer than the proxy's current hard-coded 60 second upstream timeout for `/backend-api/codex/responses/compact`. Even after `session_id` routing affinity from PR #143 is applied, those longer-running compact calls still fail with `502 Bad Gateway` and `Timeout on reading data from socket`.

Using the full streaming idle budget for compact requests fixes the crash, but in practice it makes Codex wait far too long before surfacing a stalled compaction. Compact requests need a longer budget than 60 seconds, but they should keep an independently tunable timeout instead of inheriting the much larger streaming budget.

## What Changes

- Remove the hard-coded 60 second compact upstream read budget.
- Introduce a dedicated `compact_upstream_read_timeout_seconds` setting for upstream compact socket reads.
- Default the compact read timeout to 120 seconds so compaction can exceed the legacy limit without stretching to the streaming idle timeout.
- Add regression coverage proving compact requests use the dedicated timeout budget.

## Impact

- Code: `app/core/clients/proxy.py`, `app/core/config/settings.py`
- Tests: `tests/unit/test_proxy_utils.py`, `tests/unit/test_settings_firewall.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`

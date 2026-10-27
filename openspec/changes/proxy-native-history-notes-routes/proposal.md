# Proposal: proxy-native-history-notes-routes

## Why

Current Codex clients enable the native history-and-notes extension, which
calls `alpha/history/v2/*` and `alpha/notes/v2/*`. codex-lb does not expose
those routes, so every call fails at the proxy before reaching upstream.

The native request has no session header. codex-rs
(`ext/history-notes/src/backend.rs`) injects a plaintext `context` object
with `session_id` and `current_agent_name` into every JSON body;
`x-openai-encrypted-tool-arguments` is a flag header on four of the ten
operations and does not encrypt the body. History and notes are account-local
upstream state, so once a session's notes live on one account, calls for that
session must keep reaching that account and must not be retried elsewhere.

## What Changes

- Expose the four native history routes and six native notes routes through the
  existing authenticated Codex-control proxy, forwarding the body and accepted
  native headers unchanged.
- Use a nonblank body `context.session_id` solely as a dedicated hard
  `history_session` affinity identity for these routes. The first call for a
  session prefers the account its Responses traffic currently runs on (the
  soft process-session owner) as a seed, then persists its own row. That row
  has no legacy raw-row interpretation and never spills over an account cap.
- Native history-and-notes calls never fail over or retry on another account,
  including when the body carries no usable identity.
- Ordinary Responses and compact routing is unchanged. The
  `history_ingest_requested` turn-metadata marker is not a routing input; a
  marked turn keeps the same soft locality as an unmarked one.

## Capabilities

### New Capabilities

- `native-history-notes-proxy`: authenticated passthrough for Codex native
  history and notes v2 operations.

### Modified Capabilities

- `upstream-proxy-routing`: specifies account affinity and no-cross-account
  retry for native history-and-notes operations.

## Impact

- `app/modules/proxy/sticky_repository.py`: `_ContinuitySource` gains
  `history_session`; it is the single owner of the literal and
  `affinity._CodexSessionSource` aliases it.
- `app/modules/proxy/affinity.py`: `history_session` selection namespace and
  the control-request policy built from the body session identity.
- `app/modules/proxy/api.py`: route registration and extraction of the native
  body session identity.
- `app/modules/proxy/_service/codex_control.py`: no cross-account retry for
  these operations.
- `app/modules/proxy/affinity_observation.py`: request logs label the source.
- Tests: `tests/unit/test_proxy_utils.py`,
  `tests/integration/test_proxy_api_extended.py`,
  `tests/integration/test_daybreak_capability_routes.py`.

## Non-goals

- Recording which accounts ingested a session's history when Responses rotate
  across accounts, and fanning history queries out to them. A native history
  query sees the store of the account it is pinned to. Keeping Responses
  ownership soft is deliberate: it preserves quota rotation and overload
  rebinding for every session, and only the account-local history/notes calls
  fail loudly when their owner is unavailable.

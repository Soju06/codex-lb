## Why

`enforcedReasoningEffort` lets an operator replace every client choice with
one fixed effort, but it cannot express the common policy "let this key choose
among normal efforts, but do not permit max or ultra". Operators then have to
either force a single value or leave the key fully unrestricted.

## What Changes

- Add the nullable API-key field `allowedReasoningEfforts` to the dashboard
  API, persistence model, and create/edit dialogs.
- Treat a non-empty list as an explicit client-plane effort allowlist. `null`
  keeps the existing unrestricted behaviour; an empty list is rejected.
- Make the allowlist and `enforcedReasoningEffort` mutually exclusive, so the
  effective policy is unambiguous.
- Reject a disallowed explicit effort before quota reservation or upstream
  forwarding with an OpenAI-compatible `403` `reasoning_effort_not_allowed`
  error.
- Apply the shared policy after model-alias normalization and before the
  existing unsupported-effort and wire-alias rewrites on Responses, compact,
  WebSocket, and Chat Completions paths.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `api-keys`: API keys can restrict client-selected reasoning efforts without
  forcing one effort.
- `responses-api-compat`: all Responses-compatible routes enforce that
  per-key restriction before dispatch.
- `chat-completions-compat`: chat requests, including source-routed traffic,
  use the same restriction after conversion to Responses semantics.

## Impact

- Database: one nullable `TEXT` column and a mutual-exclusion constraint on
  `api_keys`; existing rows remain unrestricted with no policy backfill.
- Backend: API-key schemas, service, repository, cache-facing data shape, and
  shared proxy policy.
- Dashboard: API-key create/edit form and request schemas only; no new route,
  setting, navigation item, dependency, or README section.

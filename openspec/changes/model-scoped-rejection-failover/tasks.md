# Tasks

## 1. Classification and ownership

- [x] 1.1 Extend the canonical model-scoped rejection classifier with the exact
  `model_not_found` code, without treating ordinary `invalid_request_error` as
  retryable.
- [x] 1.2 Reuse that classifier in stream account-health handling so retries do
  not write an account error for a model-scoped rejection.
- [x] 1.3 Keep a required WebSocket owner selected and surface its original
  model rejection instead of excluding it for another account.
- [x] 1.4 Consolidate the required-owner predicate so temporary forced-refresh
  preference cannot pin a movable model-rejection replay.
- [x] 1.5 Decide pre-created replay ownership after the fresh-body prep, so a
  proxy-injected anchor is released with its pin and the legacy
  `account_model_unsupported` failover keeps working on continuation turns;
  surface the original rejection for a turn-state owner without a reconnect.

## 2. WebSocket pre-created retry

- [x] 2.1 Reuse the canonical classifier for pre-created `error` and
  `response.failed` events before `response.created`.
- [x] 2.2 Keep accepted output, file-pinned, and previous-owner requests out
  of cross-account replay.
- [x] 2.3 Preserve the original `model_not_found` envelope when bounded
  replacement selection exhausts before opening another account.

## 3. Output-free HTTP overload replay

- [x] 3.1 Defer only the replay-safe lifecycle prelude at the direct HTTP SSE
  boundary and return its output-free overload terminal to the existing retry
  owner; stop buffering once output is downstream-visible.
- [x] 3.2 Name the eligibility flag `allow_fresh_sibling_replay` at both
  `_stream_once` call sites and gate it on deterministic failover, a remaining
  sibling attempt, no continuity anchor, the account/model replacement, the
  shared hard-owner affinity predicate, dispatch/file/turn ownership, and an
  account-neutral body.
- [x] 3.3 Retain the existing health, lease, exclusion, and overload-backoff
  handling.
- [x] 3.4 Reproduce a fresh HTTP stream receiving `server_is_overloaded` or
  `overloaded_error` as either the first SSE event or after only
  `response.created`, then succeeding on excluded-account sibling B; prove
  previous-response ownership, disabled deterministic failover, emitted
  content/tool output, terminal output evidence, the single account/model
  replacement, and a thread-scoped request with a raw legacy `CODEX_SESSION`
  row remain fail closed.
- [x] 3.5 Cover the public `POST /v1/responses` and
  `POST /backend-api/codex/responses` routes: one `response.created` from the
  sibling, no duplicate prelude, terminal from the sibling.

## 4. Verification

- [x] 4.1 Cover two-account HTTP rejection health neutrality and a valid
  neighbour request; cover a movable WebSocket pre-created retry and the
  owner-bound original-404 control through the public routes.
- [x] 4.2 Run proxy architecture, cancellation, timing, settings, lint/type,
  targeted route suites, the focused stream retry regression, and strict
  change validation.
- [x] 4.3 Add route regressions for temporary refresh preference, exhausted
  WebSocket envelope retention, and HTTP 404 model-rejection exhaustion.
- [x] 4.4 Add route regressions for the anchored follow-up turn (legacy and
  `model_not_found`) replaying with the fresh body on another account, and the
  turn-state owner control that surfaces the original rejection.

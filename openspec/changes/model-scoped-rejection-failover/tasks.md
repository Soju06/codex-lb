# Tasks

## 1. Classification and ownership

- [x] 1.1 Extend the canonical model-scoped rejection classifier with the exact
  `model_not_found` code, without treating ordinary `invalid_request_error` as
  retryable.
- [x] 1.2 Reuse that classifier in stream account-health handling so retries do
  not write an account error for a model-scoped rejection.
- [x] 1.3 Keep a required WebSocket owner selected and surface its original
  model rejection instead of excluding it for another account.

## 2. WebSocket pre-created retry

- [x] 2.1 Reuse the canonical classifier for pre-created `error` and
  `response.failed` events before `response.created`.
- [x] 2.2 Keep accepted output, file-pinned, and previous-owner requests out
  of cross-account replay.

## 3. Verification

- [x] 3.1 Cover two-account HTTP rejection health neutrality and a valid
  neighbour request; cover a movable WebSocket pre-created retry and the
  owner-bound original-404 control through the public routes.
- [x] 3.2 Run proxy architecture, cancellation, timing, settings, lint/type,
  targeted route suites, and strict change validation.

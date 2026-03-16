## Overview

This change makes `previous_response_id` a proxy-local feature instead of an upstream passthrough. The proxy will persist the normalized request/response state required to reconstruct a prior conversation turn, then rebuild the upstream `input` array whenever a new request references an earlier response id.

## Decisions

### Persist snapshots in a dedicated table instead of reusing request logs

`request_logs` only stores metrics and cannot reconstruct input/output history. Add a dedicated snapshot table keyed by `response_id` with `parent_response_id`, `account_id`, normalized turn input JSON, and terminal response JSON. Keep `account_id` as plain text instead of a cascading foreign key so deleted accounts do not erase replay state.

### Replay prior turn input/output, not prior instructions

OpenAI Responses semantics do not carry forward prior `instructions` when a request uses `previous_response_id`. The resolver will recursively flatten `turn_input + prior_response.output` for each parent snapshot, then append the current request input while leaving the current request's `instructions` untouched.

### Prefer the previous account without adding a new sticky-session kind

The stored snapshot already includes the prior `account_id`, so adding a parallel sticky row would duplicate routing state. Extend account selection with an optional preferred account: use it when the account is still eligible for the current request, otherwise log the miss and fall back to the existing selection path.

### Persist snapshots from shared stream settlement state

Collected non-stream responses already reconstruct terminal `response.output` from `response.output_item.*` events, but streaming paths do not preserve that state. Move output-item accumulation into a shared helper and carry snapshot metadata through HTTP stream settlement and WebSocket request state so every successful terminal response can persist the same canonical snapshot payload.

## Verification


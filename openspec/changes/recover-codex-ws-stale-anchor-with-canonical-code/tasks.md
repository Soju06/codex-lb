## 1. Spec (this change)

- [x] 1.1 Redefine the Codex-native WebSocket stale-anchor sanitized signal as the canonical `previous_response_not_found` code (raw envelope and id stripped) in `responses-api-compat`.
- [x] 1.2 Scope the "never leak raw upstream errors" masking to the raw envelope and missing id, distinguishing the Codex-native WebSocket route (canonical code allowed) from public `/v1/responses` (`stream_incomplete` retained). Reconciles requirements 1104, 1134, 774, and 998.
- [x] 1.3 The official Codex client's full-context-retry recovery on `previous_response_not_found` is confirmed from its own source (see `design.md`'s Load-bearing assumption for citations: wire-level retry classification, the one-shot last-response channel that only populates on success, the full-history prompt rebuild on retry, and the client's own "Retrying the full request" message). Source-level proof of a structural mechanism is treated as sufficient here; this is not held open pending maintainer-side runtime verification.

## 2. Implementation

See `design.md`'s Implementation guidance for the verified scope proof and for why step 2.2 exists (it was not anticipated before implementation; it was found by a failing test, not by static analysis).

- [x] 2.1 In `app/core/errors.py:46-47`, renamed `PREVIOUS_RESPONSE_STALE_CODE`/`PREVIOUS_RESPONSE_STALE_MESSAGE` to `PREVIOUS_RESPONSE_NOT_FOUND_CODE = "previous_response_not_found"` / `PREVIOUS_RESPONSE_NOT_FOUND_MESSAGE = "Previous response was not found; retry without previous_response_id."`, and updated import sites (`service.py`, `streaming/mixin.py`, `streaming/helpers.py`, `websocket/helpers.py`) and the internal literal in `is_previous_response_not_found_error` to reference the new constant (single source of truth for the string). `_websocket_continuity_error_fields`'s branching logic, `_websocket_downstream_response_id`, and everything in `http_bridge/` were left unchanged, as planned.
- [x] 2.2 Fixed a second, independent masking layer that the original plan did not anticipate: `_wrapped_websocket_error_event` (`websocket/helpers.py`) unconditionally re-classified any already-sanitized error via `is_previous_response_not_found_error` and force-replaced it with `stream_incomplete`, which silently defeated 2.1 for the connect-failure path only (caught by `test_backend_responses_websocket_connect_failure_masks_previous_response_not_found` and `..._logs_client_supplied_stale_anchor_metadata` failing). Added `expose_stale_previous_response_classifier: bool = False` to that function (default preserves prior behavior everywhere) and threaded `request_state.expose_stale_previous_response_classifier` / `codex_session_affinity` through its two call sites that follow a `_sanitize_websocket_*previous_response_error` call. Its other call sites (malformed JSON, schema validation, missing session, generic `AppError`) are unaffected and untouched.
- [x] 2.3 Confirmed public `/v1/responses` `stream_incomplete` masking is unchanged: `expose_stale_previous_response_classifier` is reachable as `True` only from the `responses_websocket` handler (`api.py`, `@ws_router.websocket("/responses")`, prefix `/backend-api/codex`); `v1_responses_websocket`, `v1_responses`, and the HTTP bridge resolve it to `False`/default.

## 3. Coverage

- [x] 3.1 Renamed `_assert_codex_previous_response_stale_error` to `_assert_previous_response_not_found_error` in `tests/integration/test_proxy_websocket_responses.py`; it now checks `error["code"]`/`error["message"]` against the renamed constants plus `error.get("param") is None` (verified safe across every call path: none of the three sanitizer functions ever set `param` on their rewritten output). The per-call-site `"previous_response_not_found" not in json.dumps(...)` assertions (18 occurrences across 16 test functions, verified by diffing against the pre-change file) were individually triaged: 5 (one each in 5 functions) were redundant with an existing `"<stale_id>" not in ...` check on the same event and were deleted; 8 (across 6 functions — 4 with one occurrence, 2 with two, `..._for_same_anchor_followups_and_recovers` and `..._grouped_anonymous_stale_anchor_persists_diagnostics`) had no such check and were replaced with one using that test's own literal stale-anchor id (not a generic string). The remaining 5 occurrences (2 on the public `/v1` route, 3 on the transparent-full-resend-replay success path, which never surfaces an error at all) were outside this route's scope and left untouched. Renamed `test_backend_responses_websocket_never_exposes_raw_previous_response_not_found_to_client` to `..._never_exposes_raw_previous_response_id_to_client`, since the code itself is no longer raw-envelope-only after this change.
- [x] 3.2 Confirmed public `/v1/responses` WebSocket clients still receive `stream_incomplete` (both existing `/v1` tests pass unmodified). Strengthened `tests/unit/test_proxy_utils.py:22502` to assert `"previous_response_not_found" not in emitted_text` (was `"codex_previous_response_stale"`, a string that can no longer appear anywhere), guarding against classifier cross-contamination on the unrelated `missing_tool_output` masking path.
- [x] 3.3 Candidate static and OpenSpec validation passed: `uv run --frozen ruff check .` (`All checks passed!`), `uv run --frozen ruff format --check .` (`957 files already formatted`), `uv run --frozen ty check` (`All checks passed!`), and `uv run --frozen python scripts/check_proxy_architecture.py` (`proxy architecture checks passed`). With `@fission-ai/openspec@1.10.0`, `npx --yes @fission-ai/openspec@1.10.0 validate recover-codex-ws-stale-anchor-with-canonical-code --strict --no-interactive` returned `Change 'recover-codex-ws-stale-anchor-with-canonical-code' is valid`, and `npx --yes @fission-ai/openspec@1.10.0 validate --specs --strict --no-interactive` returned `Totals: 57 passed, 0 failed (57 items)`.

## 4. Parameter-less invalid previous-response variant (#1816)

- [x] 4.1 Extend the change artifacts with the exact ChatGPT backend envelope:
  `code=invalid_request_error`, message `Invalid previous_response_id.`, and no
  `param`, while preserving fail-closed behavior for unrelated invalid requests.
- [x] 4.2 Add failing unit and externally visible bridge/WebSocket regression
  coverage for classification and one-shot replay without the stale anchor.
- [x] 4.3 Extend `is_previous_response_not_found_error` narrowly so the exact
  envelope reaches the existing continuity recovery path.
- [x] 4.4 Prove public masking, retry bounds, and existing canonical error
  behavior remain unchanged with the focused and related suites.
- [ ] 4.5 Build and canary the hotfix, then verify the live stale-anchor path
  does not repeat the old anchor or enter a cooldown loop.

## 5. Verified HTTP full-resend stale-owner escape

- [x] 5.1 Extend the change artifacts with the live canary failure and require
  account-neutral replay only after an explicit stale-anchor rejection plus the
  existing durable full-resend/file-neutrality proof.
- [x] 5.2 Add an externally visible `/backend-api/codex/responses` regression
  proving the rejecting owner is excluded, the stale anchor and affinity
  headers are removed, and the replacement account completes the turn.
- [x] 5.3 Route the explicit stale-anchor recovery branch through the existing
  account-neutral projection while preserving durable operation identity and
  keeping delta/file-bound/transport-only failures unchanged.
- [x] 5.4 Focused bridge/retry validation passed: `tests/unit/test_proxy_http_bridge.py -k 'test_stream_via_http_bridge_preserves_context_after_owner_unavailable or verified_stale_anchor or retry_http_bridge_request_on_fresh_upstream or retry_http_bridge_precreated_request or http_bridge_retry_circuit'` (54 passed, 665 deselected), `tests/unit/test_bridge_ring_lifecycle.py -k 'takeover or retry_circuit or recovery_handoff'` (9 passed, 32 deselected), and `tests/integration/test_http_responses_bridge.py -k 'test_backend_responses_http_bridge_replays_verified_full_resend_after_stale_owner and (inactive-unknown-journal or inactive-unknown-owner-bound-journal)'` (2 passed, 150 deselected). Ruff, `ty`, architecture, strict OpenSpec, and `git diff --check` passed as recorded in 3.3.

## 6. Owner-bound verified full-resend recovery

- [x] 6.1 Record the production payload gap: prefix-verified full history can
  be safe to replay while retained tool/file history makes account migration
  unsafe.
- [x] 6.2 Extend the external backend Responses regression with an owner-bound
  tool-history variant that must retry on the same owner without the rejected
  `previous_response_id`.
- [x] 6.3 Split stale-anchor recovery into account-neutral migration and
  same-owner unanchored replay while preserving one-shot and operation-fence
  invariants.
- [ ] 6.4 Re-run focused/full related validation, rebuild ARM64, canary both
  replay variants, redeploy, and verify production cooldown does not recur.

## 7. Owner-pinned circuit isolation for verified same-owner replay

- [x] 7.1 Record the production carry-over where the same-owner recovery branch
  ran but the prior hard-key durable circuit suppressed the unanchored submit.
- [x] 7.2 Seed an expired two-failure circuit in the external owner-bound
  regression and require successful verified replay without deleting it.
- [x] 7.3 Route the internally marked stale-anchor + trim-verified replay through
  a unique owner-pinned key without bypassing or deleting the original circuit;
  retain local/durable state for other requests.
- [ ] 7.4 Re-run related validation, rebuild ARM64, canary, redeploy, and verify
  the production hard key no longer emits cooldown suppression.

## 8. rvw review hardening

- [x] 8.1 Restrict parameter-less `invalid_request_error` classification to
  the exact normalized invalid-previous-response message.
- [x] 8.2 Require an explicit stale-anchor rejection plus durable retained-
  output proof before either unanchored replay path; transport-only recovery
  remains anchored/fail-closed.
- [x] 8.3 Replace retry-circuit deletion with a tightly constrained internal
  replay marker so concurrent replicas cannot lose newer circuit state.
- [x] 8.4 Prevent the operation-journal ambiguous-transport path from removing
  the anchor; only an explicit stale-anchor rejection may authorize unanchored
  replay.
- [x] 8.5 Require a registered operation id, durable session/owner fence, and
  successful spool reset before either stale-anchor replay branch.
- [x] 8.6 Preserve pre-existing local/durable circuit state when the internally
  verified replay completes successfully.
- [x] 8.7 Seed an active future-cooldown circuit and assert marker-only admission,
  local retention, no durable clear, and transport-only anchored recovery.
- [x] 8.8 Fail closed when an UNKNOWN recovery journal belongs to an inactive
  durable owner; do not claim it for unanchored account-neutral replay.
- [x] 8.9 Require `replay_count == 0` on all stale-anchor replacement branches.
- [x] 8.10 Keep explicit stale-anchor replacement independent from recovery-
  journal claims so deterministic settlement cannot race replacement ownership.
- [x] 8.11 Preserve retry-circuit state independently while clearing quarantine
  after a verified replay succeeds.
- [x] 8.12 Fail closed on every explicit stale rejection after a prior replay,
  including delta-only and prefix-unverified inputs.
- [x] 8.13 Reject present blank/whitespace `param` values using the typed
  `OpenAIErrorParam` presence state; trimming may yield a non-matching empty
  normalized value, but it must not become absent.
- [x] 8.14 Reuse the pending-tool-manifest safe-context proof for stale-anchor
  replay eligibility.
- [x] 8.15 Preserve the typed `OpenAIErrorParam` presence bit and raw JSON
  value, including blank/whitespace strings, through shared bridge/WebSocket
  extraction and prevent canonical stale rewriting; do not synthesize a
  replacement value.
- [x] 8.16 Make verified stale-anchor replacements ineligible for clean-close
  and all other transport-level redispatch.
- [x] 8.17 For account-neutral replay, capture and compare retry-circuit
  generation so only the circuit observed at authorization may be claimed.
- [x] 8.18 Preserve blank parameter presence and raw JSON through HTTP-bridge
  terminal normalization and common internal error-envelope construction
  without losing its typed presence state. Keep that malformed state internal
  for matching and fail-closed authorization; client-facing Responses
  serializers omit blank, whitespace, null, and non-string params while
  trimming valid strings.
- [x] 8.19 Compare account-neutral authorization generation against the original
  hard key for every circuit state, including below-threshold and expired rows.
- [x] 8.20 Block verified stale-anchor replacements from auth replay.
- [x] 8.21 Apply inactive UNKNOWN journal inspection to owner-bound safe-context
  replay as well as account-neutral replay.
- [x] 8.22 Separate durable operation `created` and `rebound` state and restore
  rebound rows instead of deleting them on pre-dispatch failure.
- [x] 8.23 Centralize denial of transport-only unanchored proof-gated replay.
- [x] 8.24 Add failpoint regressions and run related full validation.
- [x] 8.25 Create a clean commit boundary between canonical signaling and HTTP
  recovery hardening before PR creation.
- [x] 8.26 Restore prior durable ownership fields when an undispatched rebound
  operation rolls back.
- [x] 8.27 Move same-owner verified replay to a unique owner-pinned internal key
  and remove its dependency on capturing or consuming the original circuit
  generation.
- [x] 8.28 Run related validation and obtain a clean rvw re-review.
- [x] 8.29 Preserve owner-pinned internal keys in the presence of normal
  `http_turn_*` headers and cover the production-shaped header path.
- [x] 8.30 Linearize account-neutral replay admission with a durable
  retry-circuit generation CAS and local failure lock.
- [x] 8.31 Fail closed after any explicit stale rejection with a consumed replay
  budget, including after partial response output.
- [x] 8.32 Clear quarantine for the original hard key after a successful
  replacement while retaining its retry-circuit evidence.
- [x] 8.33 Reject present non-string/null error params across raw HTTP and
  WebSocket normalization as malformed `OpenAIErrorParam` values; retain their
  raw JSON and never collapse them into absence or a replacement value.
- [x] 8.34 Store replay admission generation independently from retry-circuit
  failure timestamps and verify delayed clock-skewed failures still merge.
- [x] 8.35 Preserve invalid-param presence and raw JSON through parsed nested
  errors and raw `response.failed` envelopes without changing the shared error
  parser contract.
- [x] 8.36 Keep restored rebound operation identity for capacity/gate retries
  and force a durable rebind before dispatch.
- [x] 8.37 Generation-fence original-key quarantine clearing and fail closed on
  explicit stale rejection after any output.
- [x] 8.38 Split public `/v1` masking from the fail-closed recovery classifier so
  every canonical `previous_response_not_found` is masked as `stream_incomplete`
  even when its `param` is malformed, and cover all nine param shapes on the
  streaming HTTP, WebSocket, and non-streaming HTTP surfaces.
- [x] 8.39 Add Alembic coverage for the retry-circuit admission-generation
  revision: fresh and legacy upgrades default to `0`, downgrade drops the
  column, re-upgrade restores it, and the graph keeps a single head.
- [x] 8.40 Parse nested `response.failed` startup errors through the shared
  presence-aware parser and prove malformed canonical params remain canonical
  internally while public startup responses mask them as `stream_incomplete`.
- [x] 8.41 Issue quarantine generations from a service-wide monotonic counter
  and prove TTL pruning plus key reuse cannot recycle a generation.
- [x] 8.42 Prove pre-dispatch rollback restores prior session, account, model,
  and parent ownership after a real cross-session durable rebind, and assert the
  submit caller forwards the captured fields.
- [x] 8.43 Reject unsupported retry-circuit claim dialects before either INSERT
  or UPDATE statement construction/execution.
- [x] 8.44 Align every change artifact with the current replay safety model:
  original-key generation CAS is account-neutral only, while same-owner replay
  uses a unique owner-pinned key and does not consume that generation.
- [x] 8.45 Keep `OpenAIErrorParam.present/raw` intact through internal parsing,
  matching, and replay authorization, while normalizing or omitting malformed
  `param` values at every client-facing Responses serializer.
- [x] 8.46 Separate public stale-shape masking and anonymous request matching
  from strict replay authorization; malformed canonical stale frames are
  claimed and masked without reconnecting or authorizing recovery, including
  multi-pending HTTP bridge and direct WebSocket cases.
- [x] 8.47 Preserve the current downstream `response.failed` id when public
  masking rewrites a stale upstream error, while removing stale ids and raw
  upstream details.
- [x] 8.48 Fence primary quarantine cleanup by completing-session identity and
  the canonical session registry, preserving a newer replacement generation
  when a detached predecessor completes.
- [x] 8.49 Fail closed on durable retry-circuit lookup failure: retain local
  admission state and skip an unfenced durable clear so a concurrent newer
  failure survives.
- [x] 8.50 Assert the retry-circuit migration's `admission_generation`
  nullability and the delayed-failure `updated_at_epoch` contract.
- [x] 8.51 Deny recovery-origin quarantine clearing when no generation was
  observed at authorization, covering the distinct-key and same-key shapes.
- [x] 8.52 Canonicalize `param` inside `response_failed_event` and the public
  client error-detail serializers so no raw malformed, blank, or non-string
  value reaches a client, while valid values stay trimmed and
  code/message/type are preserved.
- [x] 8.53 Use top-level `error_type` rather than the `type: "error"` frame
  discriminator when the shared HTTP-bridge error parser reads a top-level
  error frame, with direct shared-parser coverage.
- [x] 8.54 Collapse the duplicated payload-error derivation in
  `http_bridge/helpers.py` into one lookup without changing event-over-payload
  precedence.
- [x] 8.55 Carry the client param canonicalization, fenced retry-circuit clear,
  and quarantine generation fencing requirements in the change delta so
  archive/sync preserves them in the main spec.
- [x] 8.56 Record the continuity fail-closed decision on the ungrouped
  malformed stale-anchor WebSocket path with surface `websocket_stream`, so the
  unmatched frame is as observable as the grouped one.

## 9. Exact-head review 5000818761

- [x] 9.1 Define the full-context retry boundary normatively: the proxy may
  replay only a retained self-contained retry-safe body (every
  `function_call_output` / `custom_tool_call_output` / `apply_patch_call_output`
  paired with its tool call in the same payload), at most once and without
  `previous_response_id`; an output-only body MUST NOT be replayed as a fresh
  turn and instead fails closed with the sanitized canonical
  `previous_response_not_found` so the compatible client resends full context.
  Public `/v1` masking and the same boundary are preserved. The requirement
  previously read as an unconditional "one full-context retry MUST be attempted"
  and contradicted the existing output-only fail-closed requirement.
- [x] 9.2 Stop classifying ownership failures as confirmed stale-anchor
  rejections. `previous_response_not_found` is reserved for a confirmed upstream
  rejection of the request's own `previous_response_id`; unprovable
  ring/durable/request-log ownership uses the separate retryable
  `upstream_unavailable`, `previous_response_owner_unavailable`, or
  `bridge_owner_unreachable` error on every route and is recorded with a
  distinct continuity reason. Carried in the change delta so sync/archive keeps
  it in the main spec.
- [x] 9.3 Cover both boundaries at the failing surfaces: the Codex-native
  `/backend-api/codex/responses` WebSocket owner-lookup failure asserts
  `upstream_unavailable` (never `previous_response_not_found`), and the
  retry-safety classifier asserts output-only rejection plus matched-call
  acceptance for all three tool-output item types.

## 10. Exact-head review 5000900294

- [x] 10.1 Align the dead-owner contract across the `responses-api-compat`
  change delta, main spec, implementation, and regression: an unreplayable
  client anchor whose durable owner is dead returns the retryable
  `previous_response_owner_unavailable` error, records continuity reason
  `owner_account_unavailable`, and never uses `previous_response_not_found`.
- [x] 10.2 Align the main and change-delta quarantine requirements: quarantine
  handling may detach a session and invoke ordinary account selection, but
  MUST NOT itself mutate account health, routing score, eligibility, or durable
  ownership or add a quarantine-specific health penalty.

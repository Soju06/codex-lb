## Context

The Codex-native `/backend-api/codex/responses` WebSocket path masks upstream `previous_response_not_found` and emits `codex_previous_response_stale` in a terminal `response.failed`, intending the client to soft-reset and retry without the anchor. Observed behavior (pi 0.82.1 / pi-ai 0.82.1) is that the client recovers by matching the error *code*: its one-shot retry fires on `error.code == "previous_response_not_found"` and nothing else, then reconnects and resends full context without `previous_response_id`. The proxy-specific `codex_previous_response_stale` code matches no client recovery path, so the turn ends and only a full client restart recovers.

This route already has a working transparent server-side replay for `previous_response_not_found` that requires no client cooperation at all: when the client's own payload is itself a self-contained full resend, `websocket/mixin.py` drops the anchor, reconnects, and replays upstream without surfacing any error to the client (`retry_safe_previous_response_not_found`, gated on `fresh_upstream_request_is_retry_safe`). The rename bug this change fixes only reaches the client in the case that mechanism cannot cover, per its own comment: "A short continuation depends entirely on the upstream anchor... only full-resend payloads with a prepared fresh body can be transparently retried." That is exactly the `store:false`, delta-input continuation shape pi/pi-ai reported: the client sends only the new turn, so codex-lb has no independently-reconstructable history to replay with, and the client is the only party that holds it. A client-actionable signal is the only remaining option for this shape.

## Goals / Non-Goals

**Goals:**
- Make stale-anchor continuity loss recoverable by unmodified Codex clients on the WebSocket route.
- Preserve the hygiene intent of masking: no raw upstream error envelope and no missing response id downstream.
- Preserve semantic classification: clients can still tell stale-anchor loss apart from quota, policy, auth, and generic invalid-request failures.
- Prevent verified HTTP full resends from re-entering the same rejecting owner
  after an explicit stale-anchor error.

**Non-Goals:**
- No client changes and no change to the public `/v1` stale-anchor masking
  contract; the final repair still applies the shared public parameter
  sanitizer there so malformed error metadata cannot escape.
- `upstream_unavailable` and suppressed-duplicate `stream_incomplete` are out of scope (follow-up).
- No account switch based only on a transport close with zero observed events;
  that send remains ambiguous and can have at-least-once side effects.

## Decisions

- **Signal with the canonical `previous_response_not_found` code, sanitized.** It is the only application-level signal unmodified clients act on, and it is the code the route's own upstream uses, so it stays faithful to the Codex contract. The raw upstream envelope and the missing `resp_...` id are still stripped.
- **Refine the masking requirements to their intent.** The masking spirit is "do not leak the raw upstream error object or internal ids," not "never emit the bare code." Change the requirements from forbidding `previous_response_not_found` outright to forbidding the raw envelope and id, while permitting the sanitized canonical code on the Codex-native route.
- **Deliver as an application-level error, not a transport close.** The client full-resend cascade (five full-payload resends, then a sticky WebSocket->HTTP downgrade) is triggered by transport signals (`1009` close, `413`), per the ingress-budget ops notes, not by an application-level `previous_response_not_found`. An application-level canonical code triggers the client's controlled single full-context retry instead.
- **Keep public `/v1/responses` on `stream_incomplete`.** OpenAI-compatible clients do not expect the Codex continuity code; the canonical-code signal is scoped to the Codex-native route.
- **Recognize the parameter-less ChatGPT backend variant narrowly.** Treat
  `code = "invalid_request_error"` plus the normalized message
  `Invalid previous_response_id.` as continuity loss only when `param` is absent
  or equals `previous_response_id`. Do not classify other generic invalid
  requests, and keep the existing canonical `previous_response_not_found`
  fast path unchanged.
- **Parameter-less classification is exact, not substring-based.** The broad
  `previous response` + `not found` message matcher remains valid only when
  upstream explicitly sets `param=previous_response_id`. With no parameter,
  only the normalized `Invalid previous_response_id.` sentence is accepted so
  missing-tool-output errors cannot enter stale-anchor replay.
- **Use explicit rejection plus the existing full-resend proof for HTTP
  migration.** A classified `previous_response_not_found` proves that the
  anchored attempt was rejected before execution. If the incoming payload also
  passes `durable_full_resend_allows_account_neutral_replay()`—including prior
  output retention, prefix fingerprint, and file-neutrality checks—the recovery
  may strip the anchor, clear stale affinity headers, exclude the rejecting
  owner, and reuse the existing one-shot account-neutral replay path. The
  durable operation identity is preserved and rebound so settlement remains
  single-owner. A bare `stream_incomplete` or no-close-frame transport failure
  is not sufficient proof and keeps the existing fail-closed circuit behavior.
- **Separate replay safety from account-migration safety.** Prefix verification
  and retained fresh request text prove that the rejected anchored request can
  be replayed without the anchor. The stricter account-neutral projection only
  decides whether that replay may move to another account. If migration is
  unsafe because retained tool/file history is owner-bound, codex-lb must still
  use the verified fresh body on the same owner rather than rebuilding another
  socket with the stale anchor. Both paths remain one-shot and preserve the
  durable operation fence; delta-only requests remain fail-closed.
- **Preserve the obsolete anchored-request circuit.** The retry circuit is keyed
  by the original hard session key and survives container replacement. An
  account-neutral replacement uses a unique internal key, but its authorization
  is still compare-and-set against the observed source-circuit generation. A
  same-owner replacement uses a distinct owner-pinned internal key and therefore
  never bypasses source-key admission. Neither path deletes the shared local or
  durable circuit row. Eventless transport failures, delta-only requests, client
  retries, and ordinary reconnects retain the existing circuit behavior.
- **Do not reuse the broad anchored-recovery predicate as replay proof.**
  `server_anchored_replay_once` intentionally treats eventless transport errors
  as eligible for an at-least-once anchored retry. Anchor removal is stricter:
  it requires an explicit classified stale-anchor rejection and the durable
  full-resend retained-output proof. Prefix trimming alone is insufficient.
- **Require the durable operation fence before anchor removal.** Both
  account-neutral and same-owner stale-anchor replay require a registered
  operation id plus the current durable session id and owner epoch. Resetting
  the operation event spool is mandatory; a disabled ledger, missing reset
  capability, or rejected owner fence falls back to the anchored fail-closed
  path without dispatching an unanchored replacement.
- **Preserve the old circuit after verified replay completion.** The verified
  marker is carried through the replacement request's terminal event. A
  successful verified replacement skips the ordinary success-driven circuit
  clear, so the older circuit and any concurrent newer failure remain intact.
  A later ordinary successful request still uses the existing settlement path.
- **Transport recovery remains anchored.** The operation-journal branch must
  not consume an ambiguous `stream_incomplete`, idle timeout, or request timeout
  as authority to remove the anchor. Only the explicit stale-anchor classifier
  may enter the unanchored replacement branches.
- **Do not claim an inactive UNKNOWN journal.** An UNKNOWN row proves that an
  earlier dispatch may have reached upstream, while an inactive lease only
  proves that its owner is gone. Claiming that row cannot turn ambiguity into
  an explicit rejection, so the request fails closed without anchor removal or
  account migration.
- **Require an unused replay budget.** All stale-anchor replacement predicates
  require `replay_count == 0`. If an eventless replay already occurred, the
  earlier attempt remains ambiguous even when the latest attempt explicitly
  rejects its anchor, so no additional anchored or unanchored replacement is
  dispatched, including delta-only and prefix-unverified requests.
- **Do not claim the ambiguous-transport journal for explicit stale recovery.**
  The terminal-event processor may deterministically settle the journal before
  the streaming catch block runs. Explicit stale recovery therefore relies on
  the already-required durable operation id/session/owner fence and mandatory
  spool reset, not a second journal claim. This removes both double-claim races
  and pre-dispatch rollback gaps around session reset.
- **Preserve typed parameter presence and raw JSON.** Error parsing carries an
  `OpenAIErrorParam` state with an explicit `present` bit and the untouched raw
  `JsonValue`. A missing `param` remains eligible for the exact
  `Invalid previous_response_id.` variant. A present blank/whitespace string,
  `null`, or other non-string value remains present and malformed: string
  normalization may produce `""` for comparison, but the raw value is never
  collapsed to absence or coerced into a replacement value. This typed state
  applies at raw bridge error parsing and the shared WebSocket event extractor
  before any canonical rewrite occurs.
- **Use the shared safe-context proof.** Stale-anchor recovery accepts either
  retained prior assistant output or an exact pending-tool-call manifest match,
  matching `classify_durable_full_resend` rather than defining a narrower
  second proof.
- **Circuit and quarantine have separate success semantics.** A verified replay
  completion skips only `_clear_http_bridge_retry_circuit`. It clears quarantine
  for both the replacement key and the stored original hard key, because
  completion disproves the recovery-origin wedge even though it must not erase
  circuit evidence from concurrent failures. Quarantine generations come from a
  service-wide monotonic counter so TTL or size-cap eviction cannot recycle a
  generation and let a stale completion clear a newer quarantine.
- **The verified replacement is the final transport attempt.** Its marker makes
  the request ineligible for clean-close replay and every other pre-created
  transport retry. `replay_count == 1` records the replacement itself, not a
  budget for one more send.
- **Claim only the approved account-neutral circuit generation.** At explicit
  rejection time, capture the original hard-key circuit's durable row fields and
  local failure state, including an explicit captured-absence state. Immediately
  before queue publication, admission snapshots the local circuit state under
  the circuit lock, releases that lock while the durable compare-and-set
  advances only the independent integer `admission_generation`, then
  revalidates the local state before publishing the request. A failure
  committed first makes the claim fail; a failure committed afterward is ordered
  after the already-admitted dispatch. This durable claim is the linearization
  point, eliminating the former lookup-to-send race without holding unrelated
  local keys behind durable I/O or hiding a delayed failure from a replica with
  a skewed wall clock.
- **Reconcile a timed-out claim instead of assuming it lost.** The claim runs
  under a timeout, and a cancelled compare-and-set says nothing about whether
  the generation was consumed. Suppressing on the timeout alone strands the
  request's one legitimate replay in the common case where the cancelled
  attempt never committed at all. Because the coordinator opens a fresh session
  per call, the reconciliation is simply re-running the identical
  compare-and-set, fenced on `admission_generation`: it can only win while the
  authorized generation is unconsumed, so it recovers the stranded replay
  without ever admitting a second one. A refused or failed reconciliation stays
  fail-closed and suppresses the replacement.
- **Compare the original hard key, independent of cooldown.** The marker stores
  the source `_HTTPBridgeSessionKey` as well as its generation. Account-neutral
  admission claims durable/local state for that source key before considering
  the replacement session. The comparison covers absent, below-threshold,
  active, half-open, and expired states; any generation that won the CAS first
  suppresses submit.
- **Block every transport resend mechanism.** In addition to clean-close retry,
  the shared auth-replay preparer rejects the verified marker before mutating
  counters or request text.
- **Preserve parameter presence and raw values through internal terminal
  normalization.** HTTP-bridge and WebSocket normalization carry an
  `OpenAIErrorParam` state, including a present blank string, JSON `null`, or
  another non-string value, through the internal terminal error envelope and
  payload used for classification, request matching, and replay authorization.
  No malformed value is collapsed to absence or coerced into a replacement
  value. Client-facing Responses serializers are a separate boundary: they
  emit only trimmed, non-empty string parameters and omit blank, whitespace,
  null, and other non-string values.
- **Reject present malformed params.** Raw HTTP and WebSocket extraction keeps
  the distinction between a missing `param` and a present value whose JSON type
  or contents cannot identify `previous_response_id`. The recovery classifier
  fails closed for those typed states, while the public masking layer remains
  independent and can still hide the stale-anchor details.
- **Inspect UNKNOWN journals for both replay variants.** Safe-context journal
  lookup is not conditioned on account neutrality; owner-bound same-account
  replay also fails closed when an inactive owner leaves an UNKNOWN attempt.
- **Distinguish operation insertion from rebind.** Durable operation snapshots
  expose separate `created` and `rebound` facts. Pre-dispatch cleanup may delete
  only a genuinely inserted row; a rebound failed operation is restored to the
  failed fence instead of deleting its durable identity.
- **Restore rebound ownership, not only state.** The transient rebound snapshot
  carries the prior session/account/model/parent fields. Pre-dispatch rollback
  restores those fields so an undispatched replacement cannot retain ownership.
- **Avoid same-owner circuit bypass.** Same-owner stale recovery receives a
  unique `internal_request_parallel` key while remaining pinned to the proven
  owner. That internal key is preserved even when the incoming header contains a
  normal `http_turn_*` value. It neither captures nor consumes the original
  hard-key generation. Account-neutral recovery keeps its unique key but still
  uses the source-circuit CAS claim above. The original hard-key circuit is
  preserved in both cases.
- **Centralize transport-only replay denial.** The pre-created retry selector
  rejects verified replacements and anchored safe-fresh candidates when no
  explicit typed replay reason exists. Clean-close, auth, and generic eventless
  transport paths cannot remove an anchor; non-transport model fallback keeps
  its explicit reason and separate policy.

## Implementation guidance

The intended upstream delivery is split into the canonical WebSocket signal PR
and an HTTP recovery transaction PR as documented in `split-plan.md`; the HTTP
PR is stacked on the canonical PR because shared classifier/normalization seams
overlap. This repository candidate combines the selected coherent fixes into
one reviewed rollout so their shared seams and recovery invariants are verified
together. That delivery boundary does not assert upstream maintainer approval
or change the upstream split plan.

The mechanism this change touches is shared code, so this section exists to keep the change from growing beyond its intended scope. Each claim below was checked against the current codebase (and, where noted, against a failing test), not assumed.

- **The core change is a two-constant rename+value swap in `app/core/errors.py:46-47`**: `PREVIOUS_RESPONSE_STALE_CODE`/`PREVIOUS_RESPONSE_STALE_MESSAGE` became `PREVIOUS_RESPONSE_NOT_FOUND_CODE = "previous_response_not_found"` / `PREVIOUS_RESPONSE_NOT_FOUND_MESSAGE`, not a rewrite of `_websocket_continuity_error_fields` (`app/modules/proxy/_service/websocket/helpers.py:1060`). That function already branches on a boolean (`expose_stale_previous_response_classifier`) and returns those two constants only when it is set; the branching logic itself is unchanged. Import sites requiring the rename: `service.py`, `streaming/mixin.py`, `streaming/helpers.py`, `websocket/helpers.py`.
- **A second, independent masking layer was missed by static analysis and only surfaced by running the tests: `_wrapped_websocket_error_event` (`websocket/helpers.py:1723`).** This function serializes every top-level `"type": "error"` frame (connect failures, pre-request-state parsing failures) and, on its own, re-classifies *any* payload it sees via `is_previous_response_not_found_error` and force-replaces it with `stream_incomplete` — regardless of whether the caller already sanitized it. Before this change that was harmless, because the already-sanitized code was `codex_previous_response_stale`, which this second check never matched. Once the sanitized code became the canonical `previous_response_not_found` itself, this independent safety net caught it and silently re-masked it back to `stream_incomplete`, defeating the fix for the connect-failure path specifically (`test_backend_responses_websocket_connect_failure_masks_previous_response_not_found` and its `..._logs_client_supplied_stale_anchor_metadata` sibling both failed this way during implementation). Fixed by adding `expose_stale_previous_response_classifier: bool = False` to `_wrapped_websocket_error_event` (default preserves today's behavior everywhere) and threading `request_state.expose_stale_previous_response_classifier` / `codex_session_affinity` through at its two call sites that follow a `_sanitize_websocket_*previous_response_error` call (`websocket/mixin.py` ~line 1015-1020 inside the connect-retry `ProxyResponseError` handler, and ~line 4615-4636 inside `_emit_websocket_connect_failure`). Its other call sites (malformed JSON, schema validation, "no active upstream session", generic `AppError`) build errors that can never classify as `previous_response_not_found`, so they correctly keep the default and are unaffected.
- **The mid-stream (`response.failed`-shaped) path never had this second-layer problem.** `_sanitize_websocket_terminal_error_fields` (the third sanitizer, used for pending-request cleanup) and `_rewrite_websocket_continuity_corruption_event` (used for in-flight `previous_response_not_found` masking) both build their output via `response_failed_event(...)` directly and send it without passing through `_wrapped_websocket_error_event` at all — confirmed by all ~14 mid-stream tests passing on the first attempt, with only the two connect-failure tests failing. `_wrapped_websocket_error_event` is specific to the top-level `"type": "error"` wire shape used before a response/request is underway.
- **The canonical WebSocket signal PR1 was scoped to the Codex-native route, not the public `/v1` or HTTP bridge.** `_websocket_continuity_error_fields` is shared: it is called from `websocket/helpers.py` and from `streaming/helpers.py:645` (`_build_stream_incomplete_terminal_event_for_request`), which is in turn called from `http_bridge/upstream_events.py` and `http_bridge/service_stubs.py`. Every call site passes `request_state.expose_stale_previous_response_classifier`, and that field has exactly three set-sites, all in `websocket/mixin.py`, all set to `codex_session_affinity` (default `False` per `support.py:803`). Tracing `codex_session_affinity` to its callers in `api.py` confirms that `True` is passed from exactly one place, the `responses_websocket` handler behind `@ws_router.websocket("/responses")` (`ws_router` prefix `/backend-api/codex`). The local combined candidate also contains separately reviewed HTTP bridge recovery changes; this route-scope claim applies only to the canonical WebSocket PR1 portion. The same fencing applies to the `_wrapped_websocket_error_event` fix above, since it is gated by the identical field/flag.
- **No additional sanitization work is needed for the id/envelope.** `_websocket_downstream_response_id` (`websocket/helpers.py:571`), which supplies the `response_id` on the outgoing event regardless of which code is returned, already resolves to the current downstream/local id (`replay_downstream_response_id` or `response_id` or `request_id`) and never to `request_state.previous_response_id` (the stale anchor). "No raw envelope, no stale id, current id preserved" is an existing invariant of the surrounding code.
- **The classifier is the shared seam for issue #1816.**
  `is_previous_response_not_found_error` already gates direct WebSocket and
  HTTP-bridge recovery. Extending that pure function for the exact
  parameter-less envelope should activate the existing one-shot safe replay;
  bridge/session code changes are only warranted if the externally failing
  integration test proves the stale anchor is still reused.
- **The live canary provided that external proof.** After the parameter-less
  envelope was classified, `previous_response_recover_local` rebuilt another
  owner-bound session with the same stale anchor. Two eventless failures on
  each of two hard bridge keys then opened the 60-second retry circuit. The
  implementation therefore extends only the explicit stale-error branch in
  `_stream_via_http_bridge`; generic reader-failure handling remains unchanged.
- **The first production deployment exposed the missing owner-bound half.**
  Logs showed `store_context_input_trimmed` and a proof-gated cooldown bypass,
  but `fresh_replay_available=false` in the stale-error diagnostic and no
  `previous_response_recover_fresh_resend` event. The real nine-item payload
  retained tool history that correctly failed account-neutral projection, so
  the implementation must distinguish “safe to resend” from “safe to migrate”
  and use the former for a same-owner unanchored replay.
- **The second production deployment exposed durable circuit carry-over.** The
  same-owner recovery event fired, but `submit_retry_circuit_suppressed` blocked
  every fresh request because the prior hard-key circuit remained half-open in
  durable storage. The integration test now seeds an expired two-failure
  circuit and requires the explicit stale-anchor branch's internally marked
  one-shot request to pass admission without deleting the durable row.
- **The circuit regression uses an actually active row.** Its cooldown expires
  in the future, so ordinary half-open admission cannot make the test pass. The
  test also asserts that the local row remains and the durable clear API is not
  called after the verified replacement completes.
- **The test migration was mostly mechanical, with 2 tests needing a genuine behavioral fix, not a test update.** All matching assertions in `tests/integration/test_proxy_websocket_responses.py` funneled through one shared helper, `_assert_codex_previous_response_stale_error` (renamed `_assert_previous_response_not_found_error`), which asserts against the renamed constants dynamically, not hardcoded literal strings — updating the constants' values alone made that helper assert the new contract everywhere. A separate, per-test `"previous_response_not_found" not in json.dumps(...)` assertion existed at 18 call sites across 16 test functions (a leftover from the old contract, verified by diffing against the pre-change file) and needed individual attention: 5 were redundant with an existing `"<stale_id>" not in ...` check and were deleted; 8 (across 6 functions, including both `failed_2`/`failed_3` pairs in 2 of them) had no such check and were replaced with one using that test's own literal stale-anchor id, rather than a generic string; the remaining 5 (public `/v1` route or success-only replay paths) were outside this route's scope and untouched. `tests/unit/test_proxy_utils.py:22502` was strengthened to also assert the new code's absence on an unrelated (`missing_tool_output`) masking path, guarding against classifier cross-contamination. The 2 connect-failure tests initially failed for the structural reason above, not because their assertions were wrong.

## Rejected Alternatives

- **Keep the nonstandard classifier (`codex_previous_response_stale`).** Current behavior; unmodified clients do not recognize it, so recovery never fires.
- **Protocol close frame (1011-style).** A transport signal, so on the official client it risks the single-message full-history resend and the `1009`/`413` retry-then-permanent-HTTP-downgrade cascade the masking exists to avoid.
- **Forward the raw upstream envelope/id.** Violates the masking hygiene intent and can leak internal ids.
- **Teach the client to recognize the proxy code.** A proxy-specific adaptation the client project would correctly reject; the deviation is on the proxy, so the fix belongs on the proxy.

## Load-bearing assumption (confirmed from source)

The official Codex client recovers from an application-level `previous_response_not_found` with a controlled retry that drops the anchor and resends full context, the same recovery the reference pi / pi-ai transport performs, and does not treat it as a transport-level cascade trigger.

This is now confirmed from the official client's own source (`openai/codex`, `codex-rs`, commit `6751b54cae32b23786001e2414d749a9916201e1`, `main` as of 2026-08-01), not merely expected by analogy to pi/pi-ai:

- `codex-rs/codex-api/src/endpoint/responses_websocket.rs` classifies `previous_response_not_found` as `ApiError::Retryable` — the same bucket as `websocket_connection_limit_reached`, the two codes the reference pi/pi-ai transport also special-cases.
- `codex-rs/core/src/client.rs`'s `get_last_response()` reads a one-shot channel that is populated only inside the `Ok(ResponseEvent::Completed { .. })` branch of the response-stream handler. Any failed attempt, including this one, drops the sender without sending, closing the channel. The next retry's `prepare_websocket_request` therefore always finds no usable prior response and sends the full `request.input` with `previous_response_id: None`. This holds structurally for any failure, not as a code-specific special case.
- `codex-rs/core/src/session/turn.rs`'s sampling retry loop independently rebuilds the retried prompt from `sess.clone_history().for_prompt(..)` (full local history), not the original request.
- Retries are on by default: `DEFAULT_STREAM_MAX_RETRIES = 5`.
- The client's own fallback message for this code, defined in its source, states the behavior directly: `"Previous response was not found. Retrying the full request."`

This was traced by reading source rather than by running the compiled client against a live server. That describes the method, not a limit on the conclusion: the mechanism is structural (a one-shot channel that cannot be populated except by a successful completion), not a heuristic inferred from observed behavior, and it is corroborated independently by the wire-level retry classification, the retry loop's own full-history prompt rebuild, and the client's own source comment describing exactly this behavior. Source-level proof of a structural guarantee is treated as sufficient confirmation of this assumption. This is not a request for the maintainer to independently runtime-verify the official client before evaluating this change; the assumption is settled unless the evidence above is contradicted.

## Final repair boundary

The follow-up repair keeps one typed error state across the internal pipeline:
`OpenAIErrorParam.present` and `.raw` remain authoritative for strict
classification, anonymous request matching, and replay authorization. Public
serialization is a separate boundary. It emits only a trimmed, non-empty
string parameter and omits null, non-string, blank, and whitespace values. A
canonical stale code with malformed metadata is therefore claimable for safe
ownership/masking, but remains ineligible for reconnect, anchor removal, or
full-history recovery. The public failure rewriter preserves a current
downstream response id while removing the stale upstream id and raw envelope.

The two concurrent cleanup paths use explicit ownership fences. Primary
quarantine cleanup compares the completing session identity against the entry
owner and leaves a newer replacement generation untouched; additional
origin-key cleanup retains its existing generation comparison. Retry-circuit
cleanup first requires a successful durable read and an observed update epoch.
On lookup failure it keeps local state and does not issue an unfenced durable
clear, so a newer durable failure remains visible to the next admission check.

The migration contract is part of the same proof: `admission_generation` is
non-nullable after both legacy and fresh upgrades, and delayed failure merges
retain the exact durable `updated_at_epoch` captured by the newer write.

## Risks / Trade-offs

- Reverses a deliberate masking decision. Mitigated by keeping the id and raw envelope stripped and scoping the canonical code to the Codex-native route only.
- If any client keys on the exact `codex_previous_response_stale` string it loses that signal. No such client is known; the code was proxy-specific and undocumented as a client contract.
- Recovery is a full-context resend, unavoidable once connection-scoped continuity is lost. codex-lb already slims oversized history to the WebSocket budget (or fails fast with `400 payload_too_large`), so this introduces no new size hazard.

- [ ] **Blocker, resolve first**: PR #2366 (`retire-recovery-dispatch-storage`)
  deletes `claim_unknown_operation_for_recovery`, the `recovery_dispatch_count`
  ORM mapping and the `expected_recovery_dispatch_count` CAS predicates as
  callerless. This change is the caller. Close #2366 or re-scope it before
  implementing the fenced lane.
- [ ] New pure module `app/modules/proxy/replay_relocation.py`:
  `RelocationInputs` / `RelocationVerdict` / `decide_relocation`. Decline order:
  downstream-visible output, then ownership facts (`single_account`, file pin,
  turn-state owner, session identity), then evidence, then the source ladder
  (client full resend -> durable transcript -> unanchored), each ending in
  `responses_payload_is_account_neutral_fresh_replay`. Closed vocabulary for
  `decline_reason`. No lenient fallback projection.
- [ ] `app/modules/proxy/replay_safety.py`: add the pure transcript rebuild —
  parent-chain assembly oldest-last, prefix-overlap dedupe against the client
  suffix, terminal-output extraction from the spooled SSE with the
  `response.output_item.done` fallback when `response.incomplete` omits
  `response.output`. Keep the module free of persistence imports by accepting
  transcript turns structurally. Use one overlap routine, not two.
- [ ] `_service/support.py`: `materialize_relocation(...)` — the only caller of
  `DurableBridgeRepository.get_replayable_transcript`; load failure is treated
  as "no transcript"; emits one structured `relocation_decision` log line.
  Add `_durable_bridge` to `_StreamingServiceProtocol`.
- [ ] Delegate the five existing bespoke implementations to the shared verdict:
  `_service/streaming/retry.py` (`_verified_cross_transport_fresh_replay`,
  `_stream_owner_bound_to`, `_move_verified_fresh_replay_from_owner` and its
  four call sites), `_service/http_bridge/streaming.py`
  (`_VerifiedDurableFullResend._verify`, `classify_durable_full_resend` and the
  replay sites), `_service/websocket/helpers.py`. Leave `_service/compact.py`
  on today's behaviour — it is a separate change.
- [ ] Fenced lane (case ②): claim through
  `claim_unknown_operation_for_recovery` with a bound of one dispatch per
  operation; require zero spooled events, no response id, and an elapsed
  dispatch age within the ambiguity window; arm the side-effect replay-dedupe
  identity on the relocated dispatch; restore the claim via
  `mark_operation_unknown(restore_recovery_dispatch_claim=True)` on
  post-claim/pre-frame failure and `rollback_operation_before_dispatch` when
  nothing was written; keep the refused-claim
  `upstream_operation_status_unknown` 503 with its cooldown hint.
- [ ] Unfenced lane (case ①): definitive evidence rolls back rather than claims;
  assert in tests that it never decrements the recovery budget.
- [ ] Constants, not settings: the relocation transcript caps, the ambiguity
  window and the one-shot bound are module constants. Confirm
  `[settings_fields].max` stays 96.
- [ ] New `tests/unit/test_replay_relocation.py`: the decision table —
  transports x sources x evidence x ownership facts, asserting `movable`,
  `source` and the exact `decline_reason` for every cell.
- [ ] `tests/unit/test_replay_safety.py`: parent-chain rebuild; overlap dedupe
  including the single-item overlap and the tail-match form; `response.incomplete`
  without `response.output` falling back to accumulated
  `response.output_item.done`; no terminal marker -> `None`; broken or cyclic
  chain -> `None`; scalar input -> `None`; unsettled tool call -> `None`;
  non-portable tool -> `None`.
- [ ] `tests/unit/test_proxy_http_bridge.py`: ① anchored delta continuation with
  an exhausted owner relocates and dispatches exactly once; ② eventless
  ambiguous failure relocates exactly once and the second attempt terminates
  with `upstream_operation_status_unknown`; ② with a spooled event declines
  **without** consuming the claim; ② past the ambiguity window declines;
  restore the concurrent-reconnect single-claim test the #2336 archive deleted;
  preflight failure after the claim refunds the budget.
- [ ] `tests/unit/test_bridge_ring_lifecycle.py`: the one-shot bound; refund
  semantics; spool reset clears `event_spool_complete` and `event_bytes`;
  `get_replayable_transcript` returns `None` after a reset.
- [ ] `tests/unit/test_proxy_errors.py`: restore the classification cases the
  #2336 archive removed, in the new shape.
- [ ] `openspec validate --specs`, `uv run ruff check`,
  `codex review --base origin/main`.

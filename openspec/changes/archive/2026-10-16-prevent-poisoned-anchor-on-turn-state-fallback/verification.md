## Verification report

Base: `3d788af3`. Scope: anchor planning for a known poisoned generated-turn-state
fallback key. No account-selection or database-schema implementation changes.

| Dimension | Result |
| --- | --- |
| Completeness | Six implementation/validation tasks complete; one requirement and five scenarios covered |
| Correctness | Full-history recovery, cached-miss retry, delta refusal, explicit anchors, and scoped fences verified at native HTTP routes |
| Coherence | Shared fallback-key derivation and existing circuit/quarantine loaders reused; submit-time race fence retained |

## Requirement and scenario evidence

`_HTTPBridgeStreamingMixin._stream_via_http_bridge` includes the scoped fallback
from `_turn_keys` in pre-planning circuit/quarantine and abandonment checks.
Its existing suppression flag reaches durable hydration and session injection.

`test_http_bridge_split_turn_state_row_honors_thread_anchor_fence` seeds real
split durable rows and aliases after a successful authenticated native request.
It uses real SQLite, API keys, and selection; only upstream sockets and token
refresh are stubbed. Its 48 cases cover two HTTP routes, native thread/legacy
session identity, poison/tombstone fences, and six request shapes:

- A full resend dispatches unchanged on its original account without the anchor,
  then completes another turn with the returned turn state.
- A cached planning miss can cause one submit-time refusal, but the next full
  resend honors the now-known fallback fence and completes instead of looping.
- A delta refuses before upstream dispatch; a client-supplied anchor is retained.
- Sibling threads and different API-key scopes retain anchored behavior.

## Validation results

- Unmodified production baseline: all four full/tombstone route/identity cases
  fail with HTTP 404 `bridge_previous_response_not_found` rather than 200.
- Modified Windows code: all 48 native cases pass; 98 existing poison/quarantine/
  tombstone/canonicalization unit tests pass.
- Windows broader HTTP/WebSocket run: 394 passed and one direct WebSocket
  sequential-reuse timing failure. That failure passes on an isolated rerun.
  The idle-reader test below was excluded from that broader run.
- Linux candidate, combined with #2627: 824 passed. This includes all 48 new
  cases, #2627's four route cases and selector regressions, HTTP/WebSocket
  transport suites, and load-balancer integration. Container networking disabled,
  synthetic accounts/SQLite only; no production volume was mounted.
- Initial Linux harness failures were missing example config files and a missing
  `UsageHistory` import when combining tests. Both harness issues were corrected
  before the 824-pass run; they required no production change.
- Ruff lint/format, affected-file typing, proxy architecture, cancellation safety,
  timing seams, settings tiers, migration topology, strict change validation,
  and all 67 main specs pass. Main requirement/context are synced.

## Explicit limitations

- `test_v1_responses_http_bridge_idle_recovery_hands_reader_to_replacement`
  times out in the modified Linux container. An independently built baseline
  with the original five production files also times out. Earlier Windows
  baseline/modified runs have the same limitation. It remains excluded and
  unresolved, not counted among the 824 passing tests.
- The Linux run reports deprecation and SQLite worker shutdown warnings.
- Full project CI, PostgreSQL, packaging, and live upstream/OAuth quota rotation
  were not run. Cloud checks/review remain separate readiness gates.
- A fence first learned at submit can still cause one refusal; the test proves
  the next complete retry does not rebuild the same poisoned attach.
- This does not fix #2591's owner-bound WebSocket connect failover, initial
  upstream failures, #2493's native error delivery, or automatic-reviewer auth.
- Candidate image exists locally; the production container and client configs
  were not replaced. Tests do not establish live deployment success.

No critical coverage/implementation gaps remain for this scoped change.
The baseline timeout and environmental warnings above remain recorded.

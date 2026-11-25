## 1. Regression

- [x] 1.1 Add `test_http_bridge_live_event_queue_applies_backpressure` through actual request preparation and upstream event relay

- [x] 1.2 Capture deterministic RED showing the unbounded queue lets a paused-consumer producer complete, while the paced control remains ordered


## 2. Bounded delivery

- [x] 2.1 Construct live HTTP-bridge event queues with the two-event terminal-safe internal capacity

- [x] 2.2 Release full-queue producer waits on downstream detachment without changing persistence or terminal settlement ownership

- [x] 2.3 Preserve completed durable replay with finite transcript-sized startup buffering

- [x] 2.4 Prove resumed and paced delivery order, terminal end marker, disconnect cancellation, settlement, and task cleanup

- [x] 2.5 Account retained live payload bytes in one fixed process-wide budget; revoke a queue when a reservation cannot be made and release bytes on dequeue


## 3. Verification

- [x] 3.1 Run the exact regression, focused bridge lifecycle tests, and relevant broader proxy tests

- [x] 3.2 Run changed-file diagnostics, Ruff, type checks, and strict OpenSpec validation

- [x] 3.3 Run an actual-path async surface driver and record bounded-pressure plus resumed-delivery output

- [x] 3.4 Review the committed diff for disconnect/cancellation, task ownership, terminal settlement, durable spool/replay, and async task leaks

- [x] 3.5 Test cross-session budget pressure, byte release after dequeue, and fail-closed queue revocation without a new operator setting


## 4. Maintainer review follow-up

- [x] 4.1 Reproduce the timeout-grace event loss, make queue-read cancellation non-consuming, and return a finished reconciled read

- [x] 4.2 Publish attached failure terminals without waiting for live capacity and prove a later session lifecycle waiter is not blocked by a stalled consumer

- [x] 4.3 Remove the unused denied-anchor generation capture helper

- [x] 4.4 Run focused and broader bridge tests, Ruff, formatting, type checks, proxy architecture validation, diff checks, strict targeted OpenSpec validation, and final exact-candidate review


## 5. Current-head review follow-up

- [x] 5.1 Add product-path regressions for budget exhaustion after the first SSE event and repeated cancellation during blocked-put cleanup

- [x] 5.2 Keep post-commit budget failure inside SSE and defer blocked-put cancellation until task reaping and reservation release finish

- [x] 5.3 Run focused and bridge integration tests, Ruff, formatting, type checks, architecture validation, diff checks, strict targeted OpenSpec validation, and exact-candidate review

## 6. Current-head reconciliation

- [x] 6.1 Bound a full live-queue enqueue by the existing bridge request deadline and preserve sibling reader settlement on expiry

- [x] 6.2 Preserve delayed-generator terminal delivery while discarding only explicitly abandoned queues

- [x] 6.3 Keep HTTP-bridge direct and routed WebSockets off native egress while preserving the native default elsewhere

- [x] 6.4 Add deadline, terminal append-exception, and direct/routed native-bypass regressions and rerun the affected proof lanes

## 7. Round-19 performance and proof

- [x] 7.1 Measure producer-ahead and interleaved queue delivery against pinned main and the delivered PR head
- [x] 7.2 Replace read-side task races with owned futures and same-task timeout cancellation; prove no child tasks and retained raced payloads
- [x] 7.3 Exercise real shared-reader dispatch and sibling deadline settlement after the paused enqueue reaches its own deadline
- [x] 7.4 Clarify the HTTP-bridge native-egress exception and record residual costs without claiming native flow control
- [x] 7.5 Record immutable benchmark evidence and run affected local verification
- [x] 7.6 Verify delayed-terminal delivery at submit, cooldown, and registration waits in both response modes; run a truncation-producing red control
- [ ] 7.7 Obtain maintainer acceptance of the native fallback and residual performance cost before merge

## 8. Upstream timing integration

- [x] 8.1 Replay the scoped queue change onto upstream `35ccf8e9`, preserving native fallback and terminal ownership
- [x] 8.2 Inject queue task ownership, enqueue clocks, and same-task timeout scopes without timing-guard exemptions
- [x] 8.3 Prove real/virtual timeout races, cancellation, delayed terminal delivery, and shared-reader deadline settlement
- [x] 8.4 Rerun affected local checks and benchmark current main, prior head, and the rebased implementation
- [x] 8.5 Record independent changed-scope review and the exact-head delivery verification boundary

The final lease-push and hosted CI/review results are recorded in the PR delivery
reply for its exact head. This implementation checklist is not a claim that
external gates have passed or that task 7.7 has been accepted.

## 9. Formal review follow-up

- [x] 9.1 Prove and fix post-submit startup cooldown cleanup of downstream attachment state and idle-session lease eligibility
- [x] 9.2 Validate the failed sender versus delayed sibling ownership finding against the real submit exception path
- [x] 9.3 Run affected tests, static checks, and independent review

New-head hosted gates remain a separate delivery requirement recorded in the
PR reply, as in section 8.

## 10. Terminal flush deadline finding

- [x] 10.1 Reproduce lost deferred output followed by successful completion through the bridge stream
- [x] 10.2 Preserve truthful terminal failure after enqueue deadline expiry without changing benign revocation ownership
- [x] 10.3 Sync the contract and run affected proof and independent review

Exact-head delivery checks remain recorded in the PR reply, separately from
the completed local implementation tasks.

## 11. Round-20 producer performance and delivery progress

- [x] 11.1 Reproduce blocked-put task overhead on the published head
- [x] 11.2 Use producer-owned capacity futures and synchronous reservation cleanup
- [x] 11.3 Prove resume, revocation, terminal publication, cancellation races, and byte accounting without child tasks
- [x] 11.4 Compare revision-pinned burst benchmarks and run affected regression and independent review
- [x] 11.5 Determine whether an established delivery-progress contract bounds shared-reader blocking, or return the policy decision without changing it

The native-egress exception remains an owner decision. No new timeout or queue
capacity policy is accepted by this performance change.

## 12. September 9 upstream composition

- [x] 12.1 Preserve native Responses interpretation and the HTTP-bridge bypass in the routed opener; retain production message and liveness/timing imports
- [x] 12.2 Prove routed Responses through the real CodexClient selects legacy on explicit bypass and interpreted native transport by default
- [x] 12.3 Validate affected bridge lifecycle, upstream integrations, specs, and independent review on the composed candidate

This reconciliation changes no delivery-stall, replay-budget, or native
acceptance policy. Exact target, candidate, and hosted evidence stay in the
PR delivery record.

## 13. Trusted routing and retry upstream composition

- [x] 13.1 Preserve trusted routing hints together with native interpretation and explicit HTTP-bridge bypass
- [x] 13.2 Prove combined routed-client dispatch and assess retry/backoff overlap with queue and completion ownership
- [x] 13.3 Run isolated affected tests, strict specs, and independent reviews on the composed candidate

The later September 10 disposition accepts live-only budget scope and the
delivery-stall failure/cleanup outcome. Numeric stall duration, native, and
performance acceptance remain open. See section 14.

## 14. Accepted delivery-stall contract

- [x] 14.1 Reconcile live-only budget scope and accepted retained-prefix/failure/EOS, reservation release, and sibling-progress requirements with the September 10 maintainer disposition
- [x] 14.2 Prepare duration-independent regression design and identify limits of existing deadline proofs in `accepted-stall-contract.md`
- [ ] 14.3 Obtain the maintainer's numeric maximum continuous delivery stall
- [ ] 14.4 Prove the public/shared-reader regression fails for the missing stall bound with request deadlines still in the future, then implement the accepted bound
- [ ] 14.5 Prove retained-prefix/failure/EOS, blocked and retained byte accounting, real reservation settlement, same-session sibling success, and cancellation/timer cleanup
- [ ] 14.6 Complete required checks, independent candidate review, and hosted verification before claiming the stall path delivered

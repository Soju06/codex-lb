Produced by fresh subagent `/root/phase_owner_proposal`. The [original verbatim report](https://github.com/Soju06/codex-lb/blob/db91d2dcce3dd76273fb8a833df1e6fdc7788995/docs/reviews/pr-2112/proposals/round-5.md) was committed before the source-link formatting correction below.

At `92ce36efe86d6cb662cd5687a21cc14b3fbef60c`, the recurring flaw groups **rounds 4 and 5**. The repair needs a total per-frame runtime owner that observes upstream phases before allowing any relay bypass, using provenance preserved from the original upstream frame.

I read the review folder in full; it contains only `review-findings.md`. I made no edits, commits, posts, or agent launches.

1. **Counted findings and grouping**

| Round | Recorded area | Finding | Relationship |
|---|---|---|---|
| 4 | upstream observation eligibility | [Exclude header-identified keepalives](https://github.com/Soju06/codex-lb/pull/2112#discussion_r4298575101) | Same flaw |
| 5 | upstream observation eligibility | [Observe the first eligible token after a keepalive](https://github.com/Soju06/codex-lb/pull/2112#issuecomment-6223644765) | Same flaw |

All five ledger entries count because none is marked invalid. The other three are **unrelated**:

- **Round 1, response ownership:** [Reject echoed client anchors](https://github.com/Soju06/codex-lb/pull/2112#discussion_r4007229422). `_publish_http_response_owner` can publish a client’s echoed `previous_response_id` as a newly owned response. That requires request-anchor comparison at the ownership publisher; the timing observer cannot establish response identity authority. The [tracked upstream witness](https://github.com/Soju06/codex-lb/issues/2274#issuecomment-5915901633) confirms this is an unchanged upstream defect. Sharing `helpers.py` and provenance terminology does not make it the timing flaw.
- **Round 2, attempt timing isolation:** [Exercise HTTP retry timing](https://github.com/Soju06/codex-lb/pull/2112#issuecomment-6223011502). The defect was missing protection against a future shared tracker. Production already creates `_HTTPPhaseLatencies()` inside each `_stream_once` invocation. Its mutation witness tests cross-attempt isolation, not frame eligibility or relay bypass.
- **Round 3, documentation publication:** [Keep review records out of public docs](https://github.com/Soju06/codex-lb/pull/2112#issuecomment-6223011502). MkDocs publication membership is unrelated to runtime phase observation.

2. **The single flaw**

**Upstream phase observation is a conditional side effect of payload parsing, while other code independently decides whether parsing—and therefore observation—is necessary. Those decisions use incomplete substitutes for actual upstream eligibility.**

The original observer used payload classification as a substitute for keepalive identity. Round 4 added the SSE header check, but the observer remained conditional on callers reaching it. The relay gate then uses settled TTFT as a substitute for settled upstream observation.

Those quantities are explicitly independent. A header-identified keepalive can legitimately preserve payload-derived TTFT while leaving first-upstream timing null. After that frame, the next real token still needs upstream observation even though TTFT is already settled.

The exact edge is visible in:

- [helpers.py:445](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/app/modules/proxy/_service/streaming/helpers.py#L445): `parse_event` owns eligibility and phase writes.
- [mixin.py:786](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/app/modules/proxy/_service/streaming/mixin.py#L786): the verbatim branch runs before `parse_event` and can `continue`.
- [support.py:347](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/app/modules/proxy/_service/support.py#L347): `_verbatim_relay_event_type` knows TTFT and pending reasoning state, but knows nothing about pending upstream observation.

A read-only execution of these production functions reproduced the state:

```text
after_keepalive:
  first_token_ms=125
  first_upstream_event_ms=None
real_delta_relay_type=response.output_text.delta
after_completion_when_delta_relayed:
  first_upstream_event_ms=1500
```

The existing independent reviewer’s route/persistence witness supplies the stronger product-path evidence: the real delta should establish `250 ms`, while the current implementation records completion at `1500 ms`.

3. **Why earlier design work missed it**

The review folder has **no `Design passes` section and no saved proposal files**. There are therefore zero formally recorded remediation design passes to count. Two relevant OpenSpec designs explain the earlier decisions:

- [Initial HTTP timing design](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/openspec/changes/archive/2026-09-05-observe-http-upstream-latency/design.md) put phase writes beside existing parsing and deliberately retained the later-token bypass. Its normal event sequence implicitly made settled TTFT imply a previously observed upstream event.
- [Round 4 keepalive design](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/openspec/changes/archive/2026-10-16-fix-http-phase-keepalive-exclusion/design.md) called `parse_event` the “sole phase observation choke point,” but it was only the sole **writer**. It was not a point every frame passed through.

Round 4 strengthened the observer’s predicate without checking the caller’s reason for skipping that predicate. Its property generator varied framing and `header`, `payload`, `both`, and `conflicting_created` representations, calling `parse_event` directly. It omitted both:

- a header keepalive whose payload establishes TTFT;
- a sequence through the production relay branch after that frame.

The route case used header keepalives with `{}`, followed by ordinary lifecycle events. Those keepalives never settled TTFT, so the later frame reached the observer. Passing those tests proved the repaired instance and framing equivalence, not the observer’s dominance over relay.

4. **Highest owner that can hold the complete rule**

Use **rung 2: one total per-attempt runtime frame-processing choke point**, owned by `_HTTPPhaseLatencies`.

Concretely, replace the split “try relay, otherwise parse and observe” arrangement with one owner operation—such as `consume_frame`—used by **both first-frame and later-frame paths**. That operation must:

1. receive authoritative original-frame provenance;
2. apply phase eligibility and pending-phase updates;
3. only then authorize verbatim relay or return parsed downstream data.

The relay helper can remain a framing/forwarding helper, but callers must not use it to infer whether timing observation is complete. The phase owner must read its own phase state directly.

Original provenance matters because service ingress is already downstream of normalization. A concrete additional sibling exists:

```text
event: codex.keepalive
data: {"error":{"code":"server_error","message":"probe","type":"server_error"}}
```

`_normalize_stream_payload_for_http_block` converts that accepted upstream frame to `response.failed`; `_format_normalized_stream_event` preserves the local response-ID distinction, but loses the original keepalive identity. A production-function execution then records `first_upstream_event_ms=125`, incorrectly.

Therefore conversions must preserve immutable original observation provenance for the owner, or observation must occur before those conversions. A post-normalization header check alone cannot enforce the rule. This is a predicted instance for the design’s catch map, not an additional counted ledger finding.

**Why rung 1 does not hold the complete rule:** the important state `(TTFT observed, first upstream unobserved)` is valid and required by the accepted contract; a type must permit it. Raw header/payload disagreement and provenance are runtime inputs. The phase fields already have one production writer, and the bypass still occurs. Read-only fields or a processed-frame type can strengthen the implementation, but cannot replace the runtime admission decision or ensure that rewritten bytes retain original meaning. Rung 3 is unnecessary because a total runtime owner can enforce the rule.

5. **Catch map**

| Finding or mechanism | What the owner must catch | Guard or generator input class |
|---|---|---|
| Round 4: header-identified keepalive | Header or original payload identifies `codex.keepalive`, regardless of downstream payload classification. Neither upstream phase changes. | One owner guard using original provenance; retain CR/LF/CRLF, optional space, effective last event field, field order, and carrier variations. |
| Round 5: TTFT settles before upstream observation | TTFT being non-null cannot authorize skipping an unobserved upstream phase. The next eligible token establishes first-upstream timing. | Owner applies observation before relay authorization. Generate sequences with token-bearing header keepalive → canonical real delta → later completion, including first-frame and later-frame placement. |
| Conversion sibling: normalization erases keepalive identity | Rewriting an error envelope must not turn excluded source traffic into eligible upstream activity. | Preserve original provenance across actual normalizers. Generate header/payload keepalive identity independently from payload classes that trigger error normalization. |
| General pending-phase behavior | First-upstream and created remain independent; zero is retained; excluded or absent phases stay null. | Generate eligible lifecycle/token events interleaved with excluded frames, repeated created events, missing created, and terminal events. |
| Fast-path preservation | Once no applicable phase observation is pending, qualifying later tokens retain lazy parsing and exact relay bytes. | Keep the existing real parse spy/byte assertions; exercise them through the new owner operation. |

Extend the existing route test with the round 5 byte sequence at its current `session.post` seam. Extend the invariant test to exercise the **combined production owner operation and real conversion path**, rather than only its parser subroutine. A property that directly feeds already-correct metadata would miss the conversion defect.

6. **Paths handling the same fact**

These are the relevant paths for the HTTP attempt’s original-frame eligibility and phase state:

| Role | Paths |
|---|---|
| Actual upstream ingress | `_iter_sse_events` in [proxy.py:1487](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/app/core/clients/proxy.py#L1487); routed HTTP loop at `3999`; direct HTTP loop at `4157`; native framed events through `NativeEgressResponse.iter_sse_events`; upstream WebSocket-to-SSE adapters `_stream_websocket_events` and `_stream_codex_websocket_events`. All can feed `_stream_once`. |
| Raw/cached payload and header interpretation | `parse_sse_data_json`, `extract_sse_data`, `extract_sse_event_type`, `sse_event_type_from_block` in [sse.py](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/app/core/utils/sse.py#L163); `classify_event_type` in [parsing.py:37](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/app/core/openai/parsing.py#L37). Payload classification and effective SSE event identity serve different contracts. |
| Provenance carriers | `ParsedSseBlock`, `format_local_sse_event`, `sse_block_with_payload`; `NativeResponsesEvent` and its native IPC reconstruction. `is_local` excludes locally generated blocks; `response_id_is_local` concerns response identity and must not itself exclude real upstream activity. |
| Conversions before observation | `_normalize_sse_event_block`, `_normalize_multi_data_sse_block`, `_normalize_stream_payload_for_http_block`, `_normalize_stream_event_payload`, `_format_normalized_stream_event` in [proxy.py:2035](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/app/core/clients/proxy.py#L2035); native Responses interpretation in [stream.rs:49](https://github.com/Soju06/codex-lb/blob/92ce36efe86d6cb662cd5687a21cc14b3fbef60c/crates/codex-lb-responses/src/stream.rs#L49). Error conversion is the demonstrated evidence-losing path. |
| Local/synthetic source evidence | `format_local_sse_event` producers in the core client; the synthetic transport-failure marker written in `app/core/errors.py`. The phase guard currently consumes both local carrier provenance and the marker. Comments, sentinels, and non-object/invalid data produce no parsed payload and must not stamp phases. |
| Attempt lifetime and anchor | The two `_stream_once` call sites in `streaming/retry.py:966` and `:2274`; fresh tracker creation in `mixin.py:522`; admission re-anchor at `:571`. These preserve attempt isolation. |
| First-frame observation | Arrival captured at `mixin.py:598`; observation at `:636` before downstream payload rewriting; TTFT assigned independently at `:775`. |
| Later-frame prediction/bypass | `_verbatim_relay_event_type` at `support.py:347`, called at `mixin.py:786`; full observation currently occurs only after that branch at `:794`; TTFT separately updates at `:931`. This is the broken ownership edge. |
| Phase writes and reads | `_HTTPPhaseLatencies.parse_event` writes first-upstream and created at `helpers.py:463–465`, reads null state at `:456–457`; `log_fields` reads them at `:468`. `first_token_ms` has separate writes at `mixin.py:776`, `:932`, and final reasoning fallback `:1038`; those cannot stand in for upstream phase state. |
| Downstream rewriting | Lifecycle/error/tool transformations and `format_sse_event` after observation in `_stream_once`. They must not reclassify original observation eligibility. |
| Persistence and export | `log_fields` → `_write_request_log` → `_persist_request_log` → `RequestLogsRepository.add` → nullable integer ORM fields. `_record_proxy_phase_latency` converts milliseconds to seconds with `/1000.0`; it preserves null exclusion and zero observations. Existing Grafana SQL reads the persisted phase columns. None of these sinks can reconstruct a missed or misclassified upstream frame. |

Separate bridge and native-WebSocket request-state writers share the persisted column names but do not consume `_HTTPPhaseLatencies` or this HTTP relay gate. They are not evidence of this recurrence and need no unrelated subsystem redesign.

GitNexus was bound to the supplied external index at this head. Its function context and relay trace agreed with source. The class-level `_HTTPPhaseLatencies` context was excessively broad/truncated, so the writer/read/bypass inventory above comes from targeted source checks rather than an absent-caller inference.


## Build

The implementation keeps the chosen rung 2 owner, `_HTTPPhaseLatencies`, and expands its existing `parse_event` operation to return the relay decision as well as parsed data. Both service frame paths call it. It observes pending phases before returning the existing canonical-frame helper's relay decision. Frames that remain excluded can still relay verbatim; an eligible frame can be observed and then relay unchanged. Once first-upstream is settled, the existing helper proves whether a frame can bypass parsing without supplying a pending lifecycle phase. TTFT remains independent; there is no caller-side copy of observation readiness. Retaining the existing method name does not change the proposed ownership.

`ParsedSseBlock.is_upstream_keepalive` is read-only. The normalizer derives that source fact before rewriting and the carrier inherits it when wrapped. Error identity provenance remains separate. Native HTTP interpretation retains raw error text and sets `python_normalization`, so the same Python normalizer owns the demonstrated native conversion without changing Rust or IPC.

Catch map: rounds 4, 5, and the identical timing finding in round 6 are covered by the combined owner. The existing route table now includes token-bearing keepalives in both first and later positions before an eligible token, plus a normalized keepalive error. The owner property uses real normalization for header/payload error cases, framing and carrier variations, settled/unsettled TTFT, first/later placement, optional/repeated created, and later relay. Existing zero, missing, retry, local-error, genuine-error, parse-spy and byte assertions remain the owning coverage for those contracts. No catch-map row is omitted.

Verification: the token route failed before production edits with 1500 ms instead of 250 ms; the normalization route and property also failed before repair. The owner property additionally generates canonical frames and rejects a repair that changes an existing verbatim relay decision while observing pending phases. After repair, 286 route/retry/SSE/native-fixture tests and 15 focused client/relay tests passed. Lint, format, types, package/wheel assets, CLI startup with HTTP 200 readiness, strict docs, and strict change validation passed. Main-spec sync and strict validation are recorded with the archived change.

Deferred work: round 1 remains in [#2274](https://github.com/Soju06/codex-lb/issues/2274#issuecomment-5915901633). The ownership publisher is structurally identical on main and this implementation; the recorded real-path witness predates this timing-only repair, and nothing here changes its cache/anchor policy. The accepted PR outcome is: “This change records those observations from the upstream stream using the existing post-admission attempt clock, request-log persistence, and Prometheus exporter.” Response ownership is outside that outcome and was not requested as part of this timing correction. No other behavior or test finding is deferred.

The round 6 navigation finding is fixed by replacing workstation-local Markdown targets with source links pinned to the reviewed repository commit. The original report remains verbatim in the linked prior commit; its proposal text and catch map are unchanged.

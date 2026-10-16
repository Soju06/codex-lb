# PR 2112 review findings

Scope: [HTTP phase timing contract](../../../openspec/specs/proxy-runtime-observability/spec.md).

## Round 1 (codex-remote, 13096d64)

- [Reject echoed client anchors](https://github.com/Soju06/codex-lb/pull/2112#discussion_r4007229422) · response ownership · app/modules/proxy/_service/streaming/helpers.py · A client-supplied continuation anchor must not establish response ownership. Deferred: unchanged upstream defect tracked in [#2274](https://github.com/Soju06/codex-lb/issues/2274#issuecomment-5915901633); the review thread records the executable witness and scope disposition.

## Round 2 (coderabbit, d56e5746)

- [Exercise HTTP retry timing](https://github.com/Soju06/codex-lb/pull/2112#issuecomment-6223011502) · attempt timing isolation · tests/integration/test_proxy_responses.py · HTTP phase timing coverage must detect observations carried from a failed attempt into a successful retry. Fixed at the existing route/persistence/metrics test: its retry case fails when `_stream_once` reuses a tracker across attempts; production already creates fresh state per attempt.

## Round 3 (ci, 58c86d05)

- [Keep review records out of the public docs](https://github.com/Soju06/codex-lb/pull/2112#issuecomment-6223011502) · documentation publication · mkdocs.yml · Repository review records must not enter the published documentation tree. Fixed at MkDocs `exclude_docs`: the strict build succeeds and its output contains no review records; public-page validation stays enabled.

## Round 4 (coderabbit, dcf1d85a)

- [Exclude header-identified keepalives](https://github.com/Soju06/codex-lb/pull/2112#discussion_r4298575101) · upstream observation eligibility · app/modules/proxy/_service/streaming/helpers.py · Keepalives must not establish upstream phase timestamps regardless of whether their type is in the SSE header or JSON payload. Fixed at `_HTTPPhaseLatencies.parse_event`: the guard reads the effective SSE event field without changing payload classification; the existing route/persistence/metrics regression and framing property fail before the repair and pass afterward.

## Round 5 (local-primary, a525d810)

- [Observe the first eligible token after a keepalive](https://github.com/Soju06/codex-lb/pull/2112#issuecomment-6223644765) · upstream observation eligibility · app/modules/proxy/_service/streaming/mixin.py · Verbatim relay must not bypass an unobserved upstream phase merely because payload classification has already established TTFT. Fixed at the total `_HTTPPhaseLatencies.parse_event` operation, with source keepalive provenance retained through normalization; the route and generated owner sequence regressions pass.

## Round 6 (coderabbit, db91d2dc)

- [Observe an eligible frame before relay](https://github.com/Soju06/codex-lb/pull/2112#pullrequestreview-5553223205) · upstream observation eligibility · app/modules/proxy/_service/streaming/mixin.py · TTFT alone must not authorize skipping a pending upstream phase. Fixed by the same total phase owner as round 5; this repeated report is part of that design pass.
- [Provide repository source links](https://github.com/Soju06/codex-lb/pull/2112#discussion_r4298947925) · review navigation · docs/reviews/pr-2112/proposals/round-5.md · Readers must be able to navigate from the saved review evidence to the referenced repository source. Fixed with repository source links pinned to the reviewed commit; the original verbatim report remains available in its prior commit.

## Design passes

- After round 5, including its repeated finding in round 6 · proposal: [round-5.md](proposals/round-5.md).

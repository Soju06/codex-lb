# PR 2112 review findings

Scope: [HTTP phase timing contract](../../../openspec/specs/proxy-runtime-observability/spec.md).

## Round 1 (codex-remote, 13096d64)

- [Reject echoed client anchors](https://github.com/Soju06/codex-lb/pull/2112#discussion_r4007229422) · response ownership · app/modules/proxy/_service/streaming/helpers.py · A client-supplied continuation anchor must not establish response ownership. Deferred: unchanged upstream defect tracked in [#2274](https://github.com/Soju06/codex-lb/issues/2274#issuecomment-5915901633); the review thread records the executable witness and scope disposition.

## Round 2 (coderabbit, d56e5746)

- [Exercise HTTP retry timing](https://github.com/Soju06/codex-lb/pull/2112#issuecomment-6223011502) · attempt timing isolation · tests/integration/test_proxy_responses.py · HTTP phase timing coverage must detect observations carried from a failed attempt into a successful retry. Fixed at the existing route/persistence/metrics test: its retry case fails when `_stream_once` reuses a tracker across attempts; production already creates fresh state per attempt.

## Round 3 (ci, 58c86d05)

- [Keep review records out of the public docs](https://github.com/Soju06/codex-lb/pull/2112#issuecomment-6223011502) · documentation publication · mkdocs.yml · Repository review records must not enter the published documentation tree. Fixed at MkDocs `exclude_docs`: the strict build succeeds and its output contains no review records; public-page validation stays enabled.

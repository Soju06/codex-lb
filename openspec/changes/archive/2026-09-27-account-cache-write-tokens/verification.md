# Verification

## Reproduction and product paths

- Real local `POST /v1/responses`, with an isolated upstream and SQLite database:
  100000 Astra cache-write tokens previously recorded 1.00 USD and settled
  1000000 microdollars. The fixed path records 1.25 USD, reports 1.25 USD in both
  the input-cost component and total, and settles 1250000 microdollars.
- Direct `/backend-api/codex/responses` WebSocket: HTTP 101 handshake followed
  by the same successful cost and settlement result.
- Mixed input (100000 input, 20000 reads, 50000 writes, 1000 output) records
  0.995 USD and settles 995000 microdollars. A duplicate upstream terminal does
  not add another log, reservation or charge.
- Streaming and non-streaming route regressions fail on the unchanged base at
  `1.00 != 1.25`. Four direct-WebSocket variants fail on the missing write count.
  Self-review also reproduced and corrected a lost write premium in the
  read-side input-cost component.
- Real HTTP boundary cases cover absent, zero, negative and excessive writes,
  the 272000/272001 context boundary, priority/flex, and unchanged GPT-5.1 cost.
  Malformed write counts follow the existing malformed cached-read behavior:
  unknown cost and a released reservation, not invented usage.
- Owned servers, sockets and temporary databases were cleaned after each run.

## Automated checks

- Existing pricing/catalog/API-key baseline: 172 passed.
- Affected cost, persistence, API-key and migration suites: 456 passed.
- Selected stream, HTTP bridge, WebSocket, reservation, cancellation and image
  pricing regressions: 45 passed.
- After correcting the read-side component, all 148 affected read-side cases
  passed again.
- Migration upgrade/downgrade/re-upgrade preserves historical costs and nullable
  counts, has one head, and leaves no schema drift.
- Changed-file Python diagnostics, `make lint`, `uv run ty check`,
  `git diff --check`, and strict validation of this change passed.

## Verification limits

Nine existing PostgreSQL-only migration cases were skipped by the local SQLite
run. Existing Starlette deprecation and SQLite expression-index reflection
warnings remain. Full local CI was not run; required GitHub checks are reported
separately on the PR.

Whole-spec strict validation reports 49 passing and 17 failing capabilities on
both the unchanged base and this change. Capability IDs, validity and issue
details are identical after excluding validation timing. This change's scoped
strict validation passes; unrelated existing specification defects are unchanged.

## Review

Self-review traced usage through native streaming, WebSocket, compact, warmup,
request persistence, cost reconstruction and API-key settlement. It confirmed
replacement rather than double charging, preserved optional-call compatibility,
and no changes to transaction, cancellation or reservation ownership rules.
Historical non-NULL costs are not repriced. External model-source and image
pricing policies are not changed.

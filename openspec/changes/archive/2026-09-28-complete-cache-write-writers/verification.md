# Verification

The automation `run-now` and quota planner `warm-now` ASGI route regressions
persist the observed write count; the former prices a 100,000-input-token
request with 20,000 cached reads and 37,000 writes at $0.365. The quota
planner stream probe parses the native event and its keyed route forwards
the count to settlement. A limit warm-up writer check covers both 37,000
writes and missing writes.

Focused tests: 237 passed; the final cost assertion and missing-count edge
then passed in focused reruns. A standalone three-writer probe persisted
nine actual log rows: positive writes cost $0.365 for each writer; absent
and zero writes cost $0.328. All three quota reservations finalized with
matching microdollar values. Real app HTTP/WS proxy regressions also passed
with their own cleanup receipts. Changed Python files have no LSP errors;
scoped Ruff check/format, wheel build and strict change/canonical spec
validation pass with pinned OpenSpec 1.11.0 (66 specs passed). Built wheel
artifacts were removed and both QA harnesses removed their temporary
database resources.

The reusable scripts and exact invocations are recorded in
`/tmp/ulw-20260928-pr2504-evidence.md`.

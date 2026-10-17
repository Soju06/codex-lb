## Local verification

Base: upstream/main `0f6a31c56ac30804ca1c0fac27ca02c6f59bf2b0`, fetched again before publication.

The public setup uses an enabled, manually registered Responses source and a
loopback HTTP provider. Both explicit compact routes initially returned
`503 no_accounts` with no native accounts. This confirms an accepted feature gap
in the existing subscription-only contract, rather than incorrect source setup.

The final focused run passed 58 cases: 30 new public compact checks and 28
request-budget checks. They cover both routes, disabled source/model ownership,
source assignment and model enforcement, history/tool preservation, output shape,
invalid responses, native registry/previous-response/file/turn-state ownership,
usage settlement, concurrent admission, errors, redaction and cancellation.

A broader run passed 394 source-forwarding, dispatch, deadline, native compact,
trigger and transport cases before the final compact-budget correction. That
correction only changes the compact source projection used for admission; the
58-case final run verifies it. Unchanged broad results are retained.

`make lint typecheck` and strict change validation pass. Foreground, background
and fixture engines were checked against one dedicated SQLite test database
before the final runs. No live database, container or provider was used.

## Requirement coverage

- Source routing and native ownership: `test_model_source_compact.py` and
  `test_model_source_compact_ownership.py` through the two public compact routes.
- Payload/output preservation and malformed responses: `test_model_source_compact.py`.
- Usage, concurrent reservation, upstream errors and departure cleanup:
  `test_model_source_compact_lifecycle.py` through HTTP and ASGI disconnect input.

## Limits

Synthetic HTTP proof does not establish actual CPA/provider compatibility.
Provider authorization, deployed-host acceptance, CPA deferred discovery and
HTTP response-history storage remain separate. Terminal-trigger and
overflow-pinned compaction are outside this explicit-endpoint change.
Hosted CI and independent review remain PR gates. An independent local reviewer
could not start because its model request returned `503 no_accounts`; no
independent-review pass is claimed.

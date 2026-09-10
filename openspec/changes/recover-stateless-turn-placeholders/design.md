## Context

The exact integrated candidate rejects a stateless native HTTP request with an echoed synthetic marker and two accounts. The identical control without the header succeeds. Current main succeeds in both cases. PR #1905 introduces the branch on HTTP bridge, raw HTTP and WebSocket paths.

## Goals / Non-Goals

Restore first-request and complete stateless replay behavior while retaining every independent ownership constraint. This does not recover opaque historical state whose owner is lost, change candidate cardinality, alter account health or change settlement.

## Decisions

Use the existing complete-request account-neutral replay validator at admission. HTTP uses the original request model; WebSocket uses its complete normalized request text. Do not project, delete or reinterpret input to make the predicate pass. Keep registered-owner resolution before the placeholder decision. Do not remove the synthetic-marker branch unconditionally, because incomplete or opaque input can still require account ownership.

Tests enter the native and public Responses routes with a synthetic transport and real account selection. Cover stateless first request, complete tool follow-up, unknown previous response, opaque state, unresolved output, registered marker and file ownership. Keep failure responses visible.

## Risks / Trade-offs

The validator is deliberately conservative. Unsupported metadata can remain fail-closed; that requires separate evidence before broadening. Passing a fresh request is not proof that an opaque resumed conversation recovered. The deployment owner must test the actual continuation chain separately.

## Migration Plan

No schema migration. Update PR #1905 and apply only this correction to the accepted integrated source. Preserve PostgreSQL data and deployment ancestry. Runtime maintenance owns deployment and post-change verification.

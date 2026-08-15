## Context

Live usage publication and account reconciliation run in different ownership
domains. The proxy captures a snapshot and enqueues it without waiting; the
single background consumer later opens its own database session. Meanwhile,
identity-aware account upsert can select canonical account `C`, reparent the
persisted children of duplicate `D`, and delete `D` in one transaction.

The loss sequence is therefore deterministic: publication records only `D`;
consolidation commits `D -> C`; `_ingest` attempts to append with stale `D`;
the account foreign key rejects the write; and the serving-safe consumer logs
and drops it. Existing history reparenting cannot cover a row that did not
exist when consolidation ran.

The relevant identity constraint is equally important: an upstream ChatGPT
account id can be shared by distinct real-email slots. Upstream identity is a
safe fallback only when it resolves to exactly one surviving local row.

## Goals / Non-Goals

**Goals:**

- Preserve every already-captured live snapshot across same-slot duplicate
  consolidation when one canonical owner survives.
- Keep publication non-blocking and persistence in the background consumer.
- Prefer a valid captured local owner; use upstream identity only to recover a
  stale or absent local owner and only when the result is unique.
- Persist each accepted snapshot once under one owner, with all represented
  windows committed atomically.
- Prove the stale-local, valid-local, and upstream-only paths without sleeps or
  timing-dependent scheduling.

**Non-Goals:**

- Changing duplicate-account selection, shared-workspace slot preservation, or
  canonical-account choice.
- Guessing between multiple local rows that share an upstream identity.
- Retrying arbitrary ingestion failures or changing the queue's drop-oldest,
  throttling, or serving-path isolation behavior.
- Adding a schema migration, configuration flag, or API response field.

## Decisions

### D1: Queue an ownership envelope containing local and upstream identities

Every proxy tap point that knows a local serving account and its upstream
ChatGPT account id will publish both. The queued item remains an in-memory typed
value containing `account_id`, `chatgpt_account_id`, and the snapshot; no
database or wire schema is introduced. Upstream-only callers continue to leave
the local id absent.

Capturing the upstream id at publication time is necessary because `D` cannot
be queried after consolidation deletes it. Looking up the upstream id only
after detecting stale `D` would already have lost the recovery key.

### D2: Select and protect the persistence owner at consume time

Ingestion will settle ownership in this order:

1. If the captured local id still identifies an account, select it even when
   the upstream identity is absent, shared, or points at another candidate.
2. If the local id is absent or no longer exists, resolve the captured upstream
   id against current account rows and select it only when exactly one row
   survives.
3. If neither rule selects an owner, do not guess; retain the current logged,
   serving-safe drop behavior.

Owner selection and the atomic append of all represented usage windows belong
to one serialized write operation. SQLite uses its writer serialization;
PostgreSQL protects the selected account row through the append transaction.
This gives the two legal orderings the same outcome: a snapshot committed
before consolidation is reparented with existing history, while a snapshot
consumed after consolidation is written directly to `C`.

The per-account fingerprint is evaluated against the selected current owner,
and the successful-write marker is updated only after the atomic append. One
queued item therefore cannot write once to stale `D` and again to `C`.

### D3: Preserve account-slot ambiguity and consolidation policy

The fallback reuses the existing unique-upstream resolution rule. Distinct
real-email slots sharing one ChatGPT workspace remain distinct and ambiguous;
the change does not merge them or choose one. Duplicate reconciliation keeps
its current email/workspace candidate filters and canonical selection. It only
needs to leave the canonical row's existing upstream identity intact, which it
already does.

This choice rejects two alternatives: always preferring upstream identity
could cross account slots even while the serving local row is valid, and
changing consolidation to force uniqueness would violate the established
shared-workspace account-slot contract.

### D4: Deterministic regression and authenticated surface QA

The regression test will capture a queued item for `D` with the shared upstream
identity, synchronously complete reconciliation to `C`, and then invoke the
ingestion step directly. It will subscribe to no timers, start no consumer
loop, and use no sleep, polling delay, or retry. Database assertions will prove
one row per represented window under `C`, no row under `D`, and no duplicate
snapshot. Separate controls prove that an existing local id wins and that an
upstream-only item still resolves uniquely.

Manual QA will use an isolated database and authenticated backend, execute a
literal `curl -i` request to `GET /api/accounts`, and verify HTTP 200, one
canonical `C`, no `D`, and the injected primary and secondary usage values.
The database diff will independently show one canonical snapshot and no
duplicate-owned history. All QA processes, credentials, database files, ports,
and temporary artifacts will be removed after capture.

## Risks / Trade-offs

- **Shared upstream id remains ambiguous.** A stale item can still be dropped
  when multiple real-email slots survive. This is deliberate: preserving slot
  ownership is safer than attributing usage to the wrong account.
- **Captured upstream identity can be absent.** A stale local-only item cannot
  be recovered. Publication call sites that know both identities are therefore
  updated together; genuinely upstream-less callers retain current behavior.
- **Settlement races consolidation.** Selecting without transaction protection
  would leave a check-then-insert foreign-key race. The serialized owner-select
  plus atomic append operation closes that gap on both supported databases.
- **Atomic append changes failure granularity.** If one represented window
  cannot be stored, none of that snapshot's windows commit. This is preferable
  to a partial snapshot and supports exactly-once settlement.

## Migration Plan

Ship publication and ingestion changes atomically. There is no schema or data
migration and no backfill: only snapshots captured after deployment carry both
identities. Rollback reverts the code; existing in-memory queued items disappear
with process shutdown exactly as they do today.

## Open Questions

None.

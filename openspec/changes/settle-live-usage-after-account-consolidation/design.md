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
to one serialized write operation. SQLite acquires `BEGIN IMMEDIATE` before
lookup and keeps its database-wide writer serialization through commit.
PostgreSQL first acquires the existing transaction-scoped advisory-lock
namespace keyed by the captured upstream identity before owner lookup. It then
reads the local owner's current identity without a row lock. When that current
non-null identity is not covered, settlement rolls back to release the initial
lock, reacquires the captured/current identities through the shared canonical
sort, and reselects the owner. This rollback is required: acquiring the current
identity while retaining the captured lock could invert the account-writer lock
order. If reconciliation wins the current-identity lock and deletes the local
row, settlement uses the last observed current identity as the unique fallback.
The reselected owner is held `FOR NO KEY UPDATE` through the append. That row
lock blocks deletion and key-changing writes without blocking the `KEY SHARE`
lock taken by concurrent foreign-key inserts. One relock is allowed; a second
identity change raises a typed terminal error, and null identities add no lock
key.

Every PostgreSQL writer that can add, replace, move, consolidate, or delete an
`Account.chatgpt_account_id` membership acquires that same upstream lock and
holds it through commit. Old and incoming non-null identities are converted to
the stable advisory keys and acquired in canonical sorted order before any
email/slot advisory locks, account row locks, fold-state lock, or writes. A
local-id writer first reads the current identity without a row lock, acquires
the sorted old/new identity locks, and then row-locks and re-reads the account;
a changed observation rolls back and repeats that lock acquisition at most
once. Upsert candidate changes use the same bounded rollback/restart before any
mutation. Membership re-reads use PostgreSQL `FOR NO KEY UPDATE`, which
stabilizes identity changes while remaining compatible with the `KEY SHARE`
locks taken by concurrent fold rollup foreign-key inserts; deletion upgrades
its lock only after acquiring the fold-state lock. The shared helper applies a
transaction-local 30-second PostgreSQL lock timeout before advisory acquisition,
so request and background transactions propagate lock contention instead of
waiting indefinitely; it performs no polling or retry.

This ordering gives both legal interleavings the same outcome: a snapshot
committed before consolidation is included when history is reparented, while a
snapshot whose current-identity reconciliation wins first relocks and writes
directly to `C` after the local duplicate disappears.
The per-account fingerprint is evaluated against the selected current owner,
and the successful-write marker is updated only after the atomic append. One
queued item therefore cannot write once to stale `D` and again to `C`.

### D3: Preserve account-slot ambiguity and consolidation policy

The fallback reuses the existing unique-upstream resolution rule. Distinct
real-email slots sharing one ChatGPT workspace remain distinct and ambiguous;
the change does not merge them or choose one. Duplicate reconciliation keeps
its current email/workspace candidate filters and canonical selection. It only
runs when the incoming upstream identity is non-null, and its duplicate query
requires `Account.chatgpt_account_id == incoming_identity`; an identity-less
local row therefore cannot be selected or deleted as an identity-reconciliation
duplicate. It only needs to leave the canonical row's existing upstream
identity intact, which it already does.

This choice rejects two alternatives: always preferring upstream identity
could cross account slots even while the serving local row is valid, and
changing consolidation to force uniqueness would violate the established
shared-workspace account-slot contract.

### D4: Deterministic regression and authenticated surface QA

The deterministic transaction regression captures a queued item for `D` with
the shared upstream identity and coordinates independent PostgreSQL sessions at
exact lock and commit events, with no sleep, polling delay, or retry. Database
assertions prove one row per represented window under `C`, no row under `D`,
and no duplicate snapshot in both transaction orderings. A composition test
also drives the real proxied SSE publication tap through the live hub and
background consumer after consolidation, awaiting the exact settlement event
with a bounded timeout. Separate controls prove that an existing local id wins
and that an upstream-only item still resolves uniquely.

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
- **Captured upstream identity can be absent.** Publication preserves the valid
  local id together with the nullable upstream field, so valid-local settlement
  still succeeds. Identity reconciliation cannot delete that identity-less row:
  reconciliation requires a non-null incoming identity and selects duplicates
  by equality to it. If the local row is already stale, no upstream fallback can
  be recovered; genuinely upstream-less callers retain that serving-safe drop.
- **Settlement races consolidation.** A selected-row lock protects a snapshot
  when settlement wins the row, but a current-identity consolidator can win
  first and delete the local owner while settlement holds only the stale
  captured-identity lock. SQLite writer serialization and PostgreSQL's bounded
  rollback/relock close both transaction orderings without acquiring locks out
  of canonical order.
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

## Why

A durable HTTP bridge row names the account that owns a Codex thread, and nothing ever takes
that name back. `accounts/repository.py::_close_http_bridge_sessions_for_account` runs only on
the `DEACTIVATED` transition; `PAUSED`, `REAUTH_REQUIRED`, `RATE_LIMITED` and `QUOTA_EXCEEDED`
all leave `account_id` in place forever. Every resume re-reads that name, requires it because
`thread_header` is hard by kind, and fails closed with 502
`previous_response_owner_unavailable` while healthy accounts sit idle. This is the remaining
class in #1707, open since 2026-08-13 with four independent reporters.

`StickySession` already solved exactly this problem and says why deleting the row is not the
answer (`sticky_repository.py:683-703`): a delete is indistinguishable from "this key was never
seen", so the anchored path keeps failing closed even after the owner recovers, because nothing
on that path can re-create the row it needs. A tombstone instead proves the owner was
deliberately abandoned, which authorizes picking a fresh one. `HttpBridgeSessionRecord` has no
counterpart — `account_id`'s `ON DELETE SET NULL` is its only release, so only deleting the
account row frees a thread.

Production, 36 hours on `1.25.0-beta.7` with 24 accounts: **1,480** of these 502s, **1,361** of
them owned by a single `reauth_required` account. `reauth_required` is in neither cleanup path
— not the `DEACTIVATED` detach, not `_HARD_STICKY_UNAVAILABLE_STATUSES`. Six `draining` rows
pinned to paused owners had been frozen for 18 hours, and all six own operation rows, so
`purge_abandoned_before`'s `~exists(operation)` guard would never have reached them. That guard
is the same one that made the 2026-09-04 incident permanent.

## What Changes

- **Schema.** `http_bridge_sessions` gains `continuity_abandoned_at` and
  `continuity_abandonment_scope`, mirroring `sticky_sessions` in name and meaning so a reviewer
  reads one pattern, not two. Both nullable, both NULL on every existing row: a rolling deploy
  keeps treating `account_id` as hard ownership until a writer retires a specific owner.
- **Retirement sweep.** `DurableBridgeRepository.retire_stale_unavailable_bridge_owners` runs on
  the existing sticky cleanup pass, with the existing grace constant
  (`_STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS`, 6h) and no new setting. Phase 1 tombstones a
  row whose owner sat in `HARD_OWNER_UNAVAILABLE_STATUSES` past its reset horizon while the row
  itself went untouched for the whole window; phase 2 deletes a tombstone that then sat another
  full window unclaimed.
- **The grace clock is `last_seen_at`, not a refreshed marker.** The sticky design needs
  `_refresh_hard_sticky_outage_grace` plus a one-shot `runtime_sentinels` startup backfill
  because `StickySession.updated_at` moves for reasons unrelated to serving. The bridge row
  already carries when the thread last actually worked, so the window is "unroutable owner **and**
  no successful turn for 6h" with no hook and no sentinel. One fewer moving part, and it is
  correct on the first sweep after deploy without a backfill.
- **`~exists(operation)` no longer blocks retirement.** It still guards phase 2, where deleting
  the row would `CASCADE` the recovery ledger. Phase 1 writes two columns and keeps every
  operation row, so gating it would reproduce 2026-09-04 exactly.
- **Retirement is applied once, in `_to_lookup`.** Every coordinator path funnels through it, so
  all four lookups are covered — the detached-row filter it sits beside had to be repeated and
  reached only two. A retired lookup keeps its identity (callers still need the session id to
  claim the row) and surrenders every piece of continuity evidence: owner, response anchor, turn
  state, pending tool calls. Dropping the anchors with the owner is what makes it coherent; an
  anchor without its account is upstream state no replacement can read.
- **A retired owner is not a missing one.** `streaming.py`'s `durable_owner_missing` exempts
  retired rows. Failing closed is right when a row should name an account and does not — that is
  lost state — but a marker is positive proof the owner was abandoned. Without the exemption,
  retiring an owner would convert one 502 into a different 502.
- **Retirement is reversible.** A successful `claim_session` clears both markers, the same way a
  sticky rebind clears its own.
- **`HARD_OWNER_UNAVAILABLE_STATUSES`** joins `ROUTABLE_STATUSES` in
  `app/modules/proxy/account_eligibility.py`, documented as usable in SQL and safe only when
  paired with a long inactivity grace — `REAUTH_REQUIRED` belongs there only under that pairing,
  since an unexpired token still makes it routable *now*.

No new `CODEX_LB_*` setting, no `.env.example` change, no README growth, no dashboard change.
None of the four ceiling-guarded proxy files grows.

## Not in this change

- **Immediate retirement on the request path.** A thread still waits out the grace window. The
  evidence-based variant — retire now when the owner cannot return before this request's own
  deadline, using `accounts.reset_at` as the proof — is the follow-up, and it needs the
  request-path CAS writer with a `SELECT ... FOR UPDATE` on the owner status that
  `sticky_repository.abandon_legacy_session_header_owner_if_unavailable` demonstrates.
- **Moving a turn whose body cannot leave its account.** Retirement stops the re-welding; it
  does not make an account-bound history replayable. A client-supplied `previous_response_id`
  with no resolvable owner still fails closed.
- **`_HARD_STICKY_UNAVAILABLE_STATUSES` omitting `REAUTH_REQUIRED`.** The same gap exists on the
  sticky side and deserves its own change rather than a drive-by edit from a bridge PR.

## One unrelated test repair, forced by the revision

`tests/integration/test_affinity_invite_migration.py` downgrades to the affinity parent and
then asserted `check_schema_drift(url) == ()`. That is not an invariant: the downgrade unapplies
everything above the topmost merge, so **any** later revision adding a column to a pre-existing
table fails it — this PR is simply the first to do so. The suite already derives `_current_head`
"so later merge revisions do not have to edit these suites"; the post-downgrade assertion is
scoped to the tables the suite governs in that same spirit. The two assertions taken at head
stay strict, and a genuine invite/affinity schema regression still fails.

## Impact

- Affected specs: `sticky-session-operations` (ADDED — owner retirement, its grace, and the
  reversibility), `database-backends` (ADDED — the two nullable columns).
- Affected code: `app/db/models.py`, one Alembic revision,
  `app/modules/proxy/durable_bridge_repository.py`,
  `app/modules/proxy/durable_bridge_coordinator.py`,
  `app/modules/proxy/account_eligibility.py`,
  `app/modules/proxy/_service/http_bridge/streaming.py`,
  `app/modules/sticky_sessions/cleanup_scheduler.py`.
- Tests: `tests/unit/test_durable_bridge_owner_retirement.py` (new),
  `tests/integration/test_repositories.py`, `tests/integration/test_migrations.py`,
  `tests/unit/test_sticky_session_cleanup_scheduler.py`,
  `tests/integration/test_http_responses_bridge.py` (the end-to-end resume), plus two new
  `POSTGRES_PYTEST_TARGETS` entries.
- Partial fix for #1707.

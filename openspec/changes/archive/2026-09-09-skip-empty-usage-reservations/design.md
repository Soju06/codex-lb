# Design

## Where the skip lives

`ApiKeysService._enforce_limits_for_request_once` builds
`reservation_items` by iterating the key's limits and appending one item
per **applicable** limit (including zero-delta items — the
"Zero-reservation limits still settle actual usage" requirement depends on
those items existing). `reservation_items` is therefore empty **iff** no
limit applies to the request. In that case the function returns `None`
before `create_usage_reservation` + `commit`.

Safety of skipping the commit: with zero applicable limits no
`try_reserve_usage` CAS ran (and a zero-delta call is read-only), and the
lazy expired-limit reset commits inside `reset_limit` itself, so there is
no pending write to lose when admission returns early. The early return
still issues a `commit()` to close the implicit transaction opened by
the admission SELECTs: proxy call sites use short-lived background
sessions, but the quota-planner warmup service holds one long-lived
session, and leaving the read transaction open would pin an
idle-in-transaction window across the warmup probe's upstream round-trip.

Why `commit()` and not `rollback()`: `AsyncSession.rollback()` expires
every tracked ORM instance **regardless of** `expire_on_commit=False`.
The warmup service shares its long-lived session with this repository and
already tracks `account` and `decision` rows; expiring them makes the
subsequent `_send_warmup_probe` access to `account.access_token_encrypted`
(and the error path's `decision.id`) raise `MissingGreenlet` — limit-free
warmups would never execute. `commit()` with `expire_on_commit=False`
(both session factories in `app/db/session.py`) leaves tracked state
loaded. It is semantically equivalent to a rollback at this point because
the open transaction holds only the admission SELECTs, and no unrelated
dirty state can be flushed by it: the proxy call sites dedicate a
fresh/scoped session to admission (`get_background_session`,
`_repo_factory`), and the quota-planner repositories commit every prior
write inside their own methods (`log_decision`, `update_decision_status`,
`claim_warmup_decision` — the latter even commits at the start of its own
transaction). Regression coverage drives the real `ApiKeysService` through
a shared session and asserts the probe's attributes stay readable.

Settlement is not a pure no-op for the ledger only: `_settle_usage_reservation`
is also the production writer of the key's last-used touch (write-behind
coalescer → `api_keys.last_used_at`). Without a reservation settlement never
runs, so the limit-free admission path records the coalescer touch itself
before returning `None` — `last_used_at` keeps advancing for limit-free keys
exactly once per admitted request, at admission time instead of stream end.
The record is in-memory (no extra commit) and sits outside
`sqlite_writer_section()` for the same reason as settlement's: the shutdown
write-through flush takes the writer section itself.

## Consumer audit (verified in code before implementation)

| Consumer | Behavior on missing reservation |
| --- | --- |
| `_settle_stream_api_key_usage` (`api_key_usage.py`) | `api_key_reservation is None` → returns `True` (settled no-op) |
| `_settle_compact_api_key_usage` | `api_key_reservation is None` → returns |
| `_release_reservation` / `_release_reservation_best_effort` / `_finalize_image_reservation` / `_settle_source_reservation` (`proxy/api.py`) | `reservation is None` → return / `True` |
| `_release_websocket_reservation` / heartbeat `_maybe_touch_api_key_reservation` / heartbeat task start | `None` → no-op |
| HTTP bridge forwarding | reservation headers only added when non-`None`; `_reservation_from_headers` returns `None` when absent |
| Bridge retry re-reservation (`http_bridge/streaming.py`) | guarded by `api_key_reservation is not None`; `begin_bridge_lifecycle` accepts `None`. With a `None` reservation, `same_reservation` (`previous is reservation`) is `True` across submit retries, so deferred account error backoffs carry over instead of resetting per re-reservation. This is the **pre-existing** lifecycle semantic for every keyless request (`api_key=None` account-direct traffic exercises `begin_bridge_lifecycle(None)` on each retry today); limit-free keyed requests now intentionally join that class. Drain-once is preserved (the dict is carried by reference and popped on drain), and the wrapper-finally early-release branch not firing for both-`None` loses nothing: releasing a `None` reservation is a no-op and non-empty `pending_backoffs` still triggers the branch via the `or`. |
| Stale-release scheduler | operates on reservation rows; limit-free admissions simply produce none |
| Quota-planner warmup | **adapted**: `reservation_id` becomes `None` when admission returns no reservation; finalize/fail calls already guard on `reservation_id is not None` |

## Interaction with `has_applicable_limits`

`ApiKeyUsageReservationData.has_applicable_limits` stays (the bridge
header round-trip and `_reservation_requires_usage` read it), but a
returned reservation now always has it `True`; `None` replaces the former
"reservation exists but has no applicable limits" state. The
`_reservation_requires_usage(reservation)` predicate is unchanged and
degenerates to `reservation is not None`.

## Rejected alternatives

- Keeping the empty INSERT with relaxed durability: still pays the
  round trips and the stream-end settlement transaction; #1665 pinned
  reservation-ledger writes to full durability, so relaxing is off-limits.
- Returning a sentinel reservation without persisting it: every consumer
  would need to learn the sentinel; `None` already has a fully audited
  no-op path (the `api_key is None` case exercises it today).

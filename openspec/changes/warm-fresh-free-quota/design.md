## Context

Account import performs an immediate usage refresh while the newly created
account still has its default per-account warm-up opt-in disabled. Enabling the
opt-in later does not preserve an "opted in at" timestamp, and the warm-up
service deliberately refuses to infer a reset from an idle monthly
`reset_at` that slides forward with each sample.

The existing paid-to-Free exception cannot cover this case safely: the
account's pre-refresh and post-refresh plans are both Free. Treating every
large `reset_at` movement as a reset would restore the duplicate warm-ups
removed by #1700.

## Goals / Non-Goals

**Goals:**

- Send one initial warm-up after an already-Free account first becomes
  eligible with a completely unused monthly quota.
- Require current-refresh evidence and preserve every existing opt-in and
  account-safety gate.
- Reuse existing persistence and cross-replica deduplication.

**Non-Goals:**

- Treating sliding `reset_at` values as confirmed resets.
- Retrying failed or skipped warm-up attempts automatically.
- Adding a migration, setting, dashboard control, or new persisted state.

## Decisions

### Use current-refresh evidence and a Free-to-Free plan snapshot

The fallback runs only after the normal reset and paid-to-Free candidates have
been rejected for the selected secondary window. It requires the current
account plan and the pre-refresh plan snapshot to normalize to `free`, a
canonical monthly sample with a reset deadline, exact zero usage, and
`recorded_at >= refresh_started_at`. The existing service gates have already
verified that the account is active, opted in globally and per account, and
that the long window is selected.

Exact zero separates the intended fresh-quota bootstrap from an old account
that has already received organic traffic. The current-refresh check prevents
stale import-time history from triggering after an unrelated refresh failure.
Requiring the pre-refresh Free snapshot prevents this fallback from bypassing
paid-to-Free safeguards.

### Enforce absence of any prior warm-up attempt inside the atomic claim

The service's latest-attempt lookup remains an early eligibility filter, but it
cannot be the concurrency boundary because another window or replica may write
an attempt after that snapshot. Initial candidates therefore request an
additional account-wide `NOT EXISTS` condition in the existing atomic insert.
SQLite evaluates it under the database writer lock. PostgreSQL serializes all
warm-up claims for the same account with a shared per-account advisory
transaction lock before evaluating the conditional insert.

During rolling upgrades, claims also retain the existing per-window advisory
lock protocol. An initial claim acquires all supported window locks in a fixed
order (`monthly`, `primary`, `primary_idle`, `secondary`) before checking for
prior attempts, so an in-flight ordinary claim from an older replica commits
before that check. Ordinary claims acquire their own window lock as before.

The attempt keeps its account, monthly window, and observed reset deadline
identity. Any durable attempt for the account closes the initial path,
including one created earlier in the same refresh or concurrently with a
different sliding monthly `reset_at`. This requires no new schema.

### Keep the candidate separate from reset confirmation

`usage_reset_confirmed` remains unchanged, so a first sample or sliding reset
deadline is still not represented as a reset. The attempt retains the
canonical monthly window and reset deadline, reusing the existing claim with
the account-wide initial-attempt guard enabled.

## Flow

```text
selected secondary refresh
        |
        v
normal confirmed reset / paid-to-Free candidate?
        | no
        v
Free before + Free now + current monthly sample + 0% + no prior attempt?
        | yes
        v
atomic account/monthly/reset claim -> send one warm-up
```

If the claim loses a race, the service does not send. A later real reset can
still use the ordinary confirmed-reset path.

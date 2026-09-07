## Context

Both `auth.json` import and untargeted OAuth creation converge on
`AccountsRepository.upsert_account_slot`. That method already distinguishes a
new local row from replacement paths and owns the transaction that makes the
row durable. Import-time usage refresh happens only after this method returns,
so this boundary can guarantee that a new binding is visible before network
work begins.

Proxy pools, memberships, endpoints, and account bindings already exist in the
schema. An explicit active binding is honored even when the global proxy toggle
is off. Route outcomes are cached by local account id and require post-commit
invalidation when binding inputs change.

## Goals / Non-Goals

**Goals:**

- Add the initial binding only on the new-row branch shared by import and OAuth.
- Keep active binding counts balanced across structurally usable pools.
- Commit the account and binding atomically and invalidate route caches only
  after a successful commit.
- Serialize concurrent automatic selectors on PostgreSQL while retaining the
  existing SQLite single-writer contract.

**Non-Goals:**

- Rebalance existing accounts when pools or bindings change.
- Probe endpoint reachability during account persistence.
- Change OAuth bootstrap routing, the global routing toggle, manual binding
  APIs, or existing bindings.
- Add a migration or operator setting.

## Decisions

### Assign at the account repository's new-row commit boundary

A focused upstream-proxy assignment helper will be called only immediately
before `upsert_account_slot` commits a newly inserted `Account`. It will attach
an `AccountProxyBinding` to that pending account without committing. The
repository then commits both records together and invalidates the route cache
after commit when assignment occurred.

Calling from the import and OAuth services separately was rejected because it
duplicates policy and creates a committed-but-unbound interval. Calling for
every upsert was rejected because a re-import could unexpectedly assign an old
unbound account or alter an intentionally inactive binding.

### Define structurally usable and least-loaded with one database query

A candidate pool is active and has at least one active membership whose
endpoint is active. The selector counts only active account bindings, orders by
that count ascending, then by pool creation time and id for a stable tie-break.
It does not perform network probes; reachability remains the endpoint test and
route-health subsystem's responsibility.

Counting inactive bindings was rejected because they do not route traffic.
Ordering by display name was rejected because names are mutable and not
required to be unique.

### Serialize concurrent automatic assignments per database

SQLite already enters its writer section before slot selection and insertion.
PostgreSQL automatic selectors will additionally acquire one transaction-local
advisory lock before reading pool loads. Different account identity locks are
already acquired earlier in a consistent order, so the shared assignment lock
serializes only the short select-and-insert portion. This prevents simultaneous
new accounts from all observing the same least-loaded pool.

Manual binding mutations remain independent and can change counts around an
automatic selection; the contract is least-loaded at the selector's database
observation, not a permanent global balance invariant.

### Preserve no-pool compatibility

If no structurally usable pool exists, the helper returns without a binding.
Imports then retain the existing routing-enabled pause behavior, while OAuth
retains its existing post-save behavior. This keeps proxy configuration
optional and avoids making account creation depend on an empty administrative
surface.

## Risks / Trade-offs

- **An endpoint can be structurally active but unreachable** → Assignment uses
  the same administrative active state as route configuration; endpoint tests
  and existing fail-closed handling remain authoritative for network health.
- **A manual binding can race an automatic assignment** → The account-binding
  uniqueness constraint protects account ownership; later additions observe
  the committed counts and converge toward balance.
- **Cache invalidation fails after commit** → The existing invalidation helper
  retains its coalesced durable retry and TTL backstop behavior.

## Migration Plan

No schema or data migration is required. Deploying the application enables the
behavior for subsequently created account rows only. Rollback removes the
automatic assignment call; bindings already created remain ordinary explicit
bindings and can be managed through the existing dashboard/API.

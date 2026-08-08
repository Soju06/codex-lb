# Make durable bridge claims atomic on SQLite

`claim_session` must allocate fencing epochs with compare-and-set semantics on
SQLite as well as PostgreSQL. The read of the owner row, epoch calculation,
and conditional update must share one serialized writer section so concurrent
claims cannot receive the same fencing token or split account and response
anchor ownership.

## Scope

- Keep PostgreSQL `SELECT ... FOR UPDATE` behavior unchanged.
- Serialize the SQLite read-modify-write and condition the update on the
  observed owner and epoch.
- Add a concurrent production-config SQLite regression covering unique epochs
  and account/anchor consistency.

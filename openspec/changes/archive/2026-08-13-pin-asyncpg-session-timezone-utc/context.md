# Context: pin-asyncpg-session-timezone-utc

The application helper `app.core.utils.time.utcnow()` returns naive UTC
datetimes. PostgreSQL `timestamptz` storage is still safe only if the client
session interprets those naive values as UTC. With asyncpg, binding a naive
Python `datetime` uses the connection's session time zone. If a deployment or
database default is `Europe/Amsterdam`, for example, a naive UTC value is
interpreted as Amsterdam local time and stored with a one- or two-hour offset.

This is especially damaging for coordinator data because the corruption is
silent: rows look valid, but comparisons against fresh `utcnow()` values are
wrong. Bridge-ring membership can look stale too early or too late, leader
election timing can drift, cleanup jobs can skip or purge the wrong rows, and
account or stream leases can be evaluated against shifted timestamps.

The fix belongs in engine construction instead of individual repository writes:
all PostgreSQL sessions must share the same interpretation before any ORM or
Core query binds a timestamp. SQLite behavior is unchanged.

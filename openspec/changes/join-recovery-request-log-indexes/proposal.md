# Join recovery and request-log index migrations

## Why

Integrating current main produces two Alembic heads: the recovery graph and
request-log missing-cost indexes. Startup `upgrade head` must remain unambiguous.

## What Changes

Add a metadata-only merge revision; preserve all existing revision identities.
Verify populated upgrades from either parent and metadata-only downgrade.

## Impact

Migration graph only. The join changes no rows or schema; the two existing
parent histories remain responsible for their own DDL and data updates.

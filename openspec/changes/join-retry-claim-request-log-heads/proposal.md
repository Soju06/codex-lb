## Why

Retry-claim receipt schema and current request-log indexes produce two Alembic heads when integrated.

## What Changes

Add a metadata-only merge revision preserving both histories. No existing revision or application schema is rewritten.

## Impact

The default head upgrade includes both features.

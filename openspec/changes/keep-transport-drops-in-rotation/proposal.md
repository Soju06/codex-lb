# Keep transport drops in rotation

## Why
Transport loss is not evidence of account failure. Transient backoff must retain a recovery candidate even for a one-account pool.

## What Changes
- Keep status-free incomplete streams account-neutral in the shared stream error handler.
- Retain the nearest-to-recovery candidate whenever only transient backoff blocks selection.

## Impact
- Affected specs: responses-api-compat
- Affected code: streaming helpers and balancer selection

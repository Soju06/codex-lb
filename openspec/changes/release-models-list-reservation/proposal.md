## Why

The model catalog endpoints reserve API-key usage before reading enabled source
models, but release only on successful return. A transient catalog database
failure or task cancellation can therefore leave the reservation charged until
the janitor runs.

## What Changes

- Ensure `GET /v1/models` and `GET /backend-api/codex/models` release their
  API-key usage reservation on normal return, exceptions, and cancellation.
- Add regression coverage for both builders when source catalog lookup fails.

## Capabilities

### Modified Capabilities

- `model-catalog-compat`: model catalog requests settle API-key reservations on
  every exit path.

## Impact

Success responses and reservation amounts are unchanged; only abandoned
reservations are released immediately.

## Why

The request metrics middleware currently emits arbitrary unmatched URL paths and raw HTTP methods as Prometheus labels. Because the SPA serves arbitrary unauthenticated GET paths, path churn creates a never-evicted metric child for every distinct path and can drive memory and scrape growth toward OOM.

## What Changes

- Preserve the existing `/v1/`, `/api/`, and `/health/` path collapse values exactly.
- Collapse every other request path to the single `/other` metric path value.
- Normalize request method labels to the finite `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, and `OPTIONS` vocabulary; map all other methods to `OTHER`.
- Add regression coverage for unmatched-path cardinality, method normalization, and the existing `/v1/` collapse.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`

## Impact

- **Code:** Prometheus request counter and duration label normalization only.
- **Compatibility:** Existing `/v1/...`, `/api/...`, `/health/...`, and bare `/health` values remain unchanged; only unmatched path and unsupported method label values change.
- **Operations:** No new settings, dependencies, or runtime behavior outside metric label values.

## Why

The zstd request-decompression path first decodes the complete body in one
native allocation and applies the configured decoded-size limit only after that
operation returns. A highly compressible request can therefore exceed the
advertised ingress memory bound before the middleware rejects it.

## What Changes

- Decode zstd request bodies through an incrementally consumed reader.
- Enforce the existing decoded-body limit before retaining each output chunk.
- Preserve the existing exact-boundary, oversized-body, invalid-encoding, and
  stacked-encoding behavior.
- Add request-level regression coverage that prevents the one-shot path from
  returning.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `http-ingress-limits`: Require zstd decoded output to remain incrementally
  bounded while decompression is in progress.

## Impact

- Middleware: `app/core/middleware/request_decompression.py`.
- Tests: `tests/unit/test_request_decompression_middleware.py` and existing
  request-decompression integration coverage.
- No settings, dependencies, schemas, routes, database, or frontend changes.

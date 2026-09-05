# Bound Request Metric Label Cardinality

## 1. Normalize request metric labels

- [x] 1.1 Preserve the existing `/v1/`, `/api/`, and `/health/` path collapse behavior, including bare `/health` behavior.
- [x] 1.2 Collapse `/backend-api/` and `/internal/` paths to bounded buckets, map all other paths to `/other`, and normalize methods to the finite supported-method vocabulary, with unsupported methods mapped to `OTHER`.

## 2. Regression coverage

- [x] 2.1 Verify many distinct unmatched paths, including an SPA-looking path, create exactly one path label value.
- [x] 2.2 Verify `/backend-api/` and `/internal/` paths use bounded buckets, including dynamic file-upload paths.
- [x] 2.3 Verify unsupported methods use the `OTHER` label.
- [x] 2.4 Verify the existing `/v1/` collapse remains unchanged.

## 3. Validation

- [x] 3.1 Run `uv run pytest tests/unit/test_metrics.py`.
- [x] 3.2 Run `uv run ruff check app/core/metrics/middleware.py tests/unit/test_metrics.py`.
- [x] 3.3 Run `openspec validate bound-request-metric-label-cardinality --strict`.

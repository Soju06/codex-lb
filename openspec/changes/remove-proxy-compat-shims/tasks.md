## 1. Verification of zero importers

- [x] 1.1 Grep `app/ tests/ scripts/ docs/ deploy/ openspec/ .github/ frontend/src` for `proxy._support`, `proxy._warmup`, and string module paths; confirm the only hits are the architecture checker, its unit test, and OpenSpec prose.

## 2. Implementation

- [x] 2.1 Delete `app/modules/proxy/_support.py` and `app/modules/proxy/_warmup.py`.
- [x] 2.2 Remove `_assert_shim_only` and `_check_service_does_not_import_shims` (and their registrations) from `scripts/check_proxy_architecture.py`.
- [x] 2.3 Drop the shim fixture files from `tests/unit/test_check_proxy_architecture.py`.
- [x] 2.4 Update `openspec/specs/proxy-architecture/context.md` so the ADR tree and fitness-function list no longer describe shims.
- [x] 2.5 Apply the MODIFIED `ProxyService remains a stable façade` requirement text to `openspec/specs/proxy-architecture/spec.md` so the main spec stays in sync with the delta.

## 3. Verification

- [x] 3.1 `make lint` (includes `scripts/check_proxy_architecture.py`) passes.
- [x] 3.2 `uv run pytest tests/unit/test_check_proxy_architecture.py -q` passes.
- [x] 3.3 `openspec validate --specs` and `openspec validate remove-proxy-compat-shims` pass.

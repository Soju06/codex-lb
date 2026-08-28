## 1. Implementation

- [x] 1.1 `DashboardGZipMiddleware` gated to `/api/` + `/assets/`; wire in `create_app`
- [x] 1.2 Immutable `Cache-Control` for `/assets/*`; hoist `StaticFiles` behind `lru_cache`
- [x] 1.3 Drop `vendor-charts` manual chunk; make `Cell` lazy in `lazy-recharts.ts`

## 2. Verification

- [x] 2.1 Frontend suite green (839 tests incl. donut/cell paths)
- [x] 2.2 Build inspection: charts chunk absent from modulepreload and entry static imports; critical path 1.70 MB → ~1.13 MB raw
- [x] 2.3 Live curl checks: asset gzip + immutable headers, index no-cache, `/backend-api/*` untouched
- [x] 2.4 Backend static/spa unit tests green; `ruff`/`ty`; `openspec validate --specs`

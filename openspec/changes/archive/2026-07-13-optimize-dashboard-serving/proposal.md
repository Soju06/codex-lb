## Why

The dashboard's first uncached load ships ~1.7 MB of JavaScript on the critical path, uncompressed: codex-lb serves the SPA with no response compression anywhere, the 572 KB recharts chunk is modulepreloaded and statically imported by the entry chunk even though every chart component is lazy (the forced `vendor-charts` manual chunk swallowed shared helper modules), and content-hashed assets are served with no `Cache-Control`, so even repeat visits renegotiate or re-download everything.

## What Changes

- Add a `DashboardGZipMiddleware` that gzips responses only for `/api/` and `/assets/` paths — proxy streaming paths (`/backend-api`, `/v1`, websockets) are never wrapped.
- Serve `/assets/*` (content-hashed by Vite) with `Cache-Control: public, max-age=31536000, immutable`; `index.html` keeps `no-cache` so deploys pick up new hashes. The per-request `StaticFiles` construction is hoisted behind an `lru_cache`.
- Drop the forced `vendor-charts` manual chunk and make the last static recharts export (`Cell`) lazy like the rest, so recharts lands in an async-only chunk loaded when a chart first renders. Verified on the built output: no modulepreload of the charts chunk, no static import from the entry chunk; recharts v3 detects the lazy `Cell` wrapper via `displayName`.
- Net effect measured on the build: critical-path JS 1.70 MB → ~1.13 MB raw, ~307 KB gzip-transferred; repeat loads serve assets from the browser cache.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `frontend-architecture`: dashboard serving MUST compress dashboard responses, serve hashed assets immutably, and keep chart vendor code off the first-paint critical path.

## Impact

- **Code**: `app/core/middleware/dashboard_gzip.py` (new), `app/main.py` (middleware + asset headers + StaticFiles hoist), `frontend/vite.config.ts`, `frontend/src/components/lazy-recharts.ts`.
- **Behavior**: response bytes/headers only; no API or visual change. Proxy paths untouched (verified live: no `content-encoding` on `/backend-api/*`).

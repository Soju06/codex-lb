## Context

Frontend audit (patch-build-revert verified): every chart component is behind `React.lazy`, but the forced `vendor-charts` rolldown group swallowed shared helper modules (CJS interop, `use-sync-external-store`, `es-toolkit`, `clsx`), so any entry-chunk module using those helpers dragged the full 572 KB chart bundle in statically, and Vite modulepreloaded it. Separately, `lazy-recharts.ts` still statically imported `Cell`, and the backend served everything uncompressed with no asset cache headers.

## Goals / Non-Goals

**Goals:** cut first-load transfer (~1.7 MB → ~307 KB gzipped critical path) and make repeat loads near-instant, with zero visual/API change and zero risk to proxy streaming.

**Non-Goals:** route-level code splitting of the six pages (separate follow-up; entry is still one chunk), self-hosting Google Fonts, pre-compressed (.br/.gz) build artifacts.

## Decisions

- **Path-gated gzip wrapper** around Starlette's `GZipMiddleware` instead of a global one: compression must never touch SSE/websocket proxy responses, and a whitelist (`/api/`, `/assets/`) is simpler to reason about than content-type sniffing. Verified live: assets gzip, `/backend-api/*` has no `content-encoding`.
- **Immutable asset caching keyed on the `assets/` prefix**: Vite content-hashes every file there, so a URL's body can never change; `index.html` keeps `no-cache` (existing behavior) so new deploys swap hashes.
- **Lazy `Cell`**: recharts v3's `findAllByType` matches children via `type.displayName`, and the lazy wrapper carries `displayName = "Cell"`, so Pie/Donut cell detection keeps working (839 frontend tests green, donut tests included).
- **No manual chunk for recharts**: letting the bundler place it produces an async-only chunk reachable solely via the dynamic `import("recharts")` in `lazy-recharts.ts` (verified in build output and dep maps).

## Risks / Trade-offs

- [First chart render pays one async chunk fetch] → intended; charts already rendered behind lazy boundaries with null fallbacks.
- [Gzip CPU on dashboard responses] → bounded to dashboard paths; assets are the dominant win and compress once per request (level 9 default of Starlette ≈ fine at these sizes).

## Migration Plan

Code-only; rollback = revert. Users' cached assets remain valid (hash-addressed).

## Open Questions

None.

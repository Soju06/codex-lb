# Verification

Against merge commit `86c508ae7`, the two new unmodified-implementation
regressions failed with `reasoning_effort_not_allowed` (absent effort and
client `ultra`); the existing high-effort case passed. After the repair,
the same three cases passed.

The scoped steering suite passed 146 tests across protocol, retention,
dispatch, real ASGI route and aiohttp transport. The route exercised missing,
client-ultra, enforced-ultra and refreshed-forbidden policy against a local
upstream WebSocket. The missing writer-transport seam produced the specified
503 without dispatch; a following ordinary send succeeded. Ruff check and
format, ty and changed-file diagnostics passed. `uv build` produced a wheel
and sdist. The pinned OpenSpec 1.11.0 validator passed this change strictly
and all 66 main specs.

The scoped route test's local listeners closed at context exit and its
temporary SQLite files and distribution output were removed. The reusable
lead QA invocation is `sh /tmp/ulw-20260928-pr2115-app-qa.sh`; it allocates
its own database and cleans it automatically. Full local CI was not repeated.
Detailed before/after evidence and cleanup receipts are in
`/tmp/ulw-20260928-pr2115-evidence.md`, outside the repository.

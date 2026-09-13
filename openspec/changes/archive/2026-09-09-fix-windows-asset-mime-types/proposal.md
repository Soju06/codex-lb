## Why

On Windows, Python's `mimetypes` merges file-type mappings from the `HKCR` registry, where third-party software commonly remaps web extensions (`.js` → `text/plain`). Starlette's `FileResponse` resolves `media_type` through `mimetypes.guess_type`, and browsers enforce strict MIME checking for ES module scripts, so on such machines every `/assets/*.js` response ships as `text/plain` and the dashboard renders as a blank page (issue #1698). macOS/Linux use the built-in table and never hit this.

## What Changes

- Pin the MIME type of every extension the built dashboard serves (`.js`, `.mjs`, `.css`, `.svg`, `.json`, `.woff`, `.woff2`, `.html`) via `mimetypes.add_type` at application import, which overrides the merged registry table on all platforms.
- No behavior change on platforms whose default table is already correct — the pinned values equal the stdlib defaults.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Dashboard delivery additionally guarantees correct web MIME types independent of the host OS's `mimetypes` registry state.

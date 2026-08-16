## 1. Fix

- [x] 1.1 Register `mimetypes.add_type` overrides for all dashboard asset extensions at `app/main.py` import, before any `FileResponse` is constructed

## 2. Tests

- [x] 2.1 Route-level regression: with a poisoned `.js -> text/plain` mapping re-registered over, `GET /assets/*.js` serves `text/javascript` (the externally failing product path from issue #1698)
- [x] 2.2 Unit: `_ensure_web_asset_mime_types()` restores every pinned extension after simulated registry poisoning

## 3. Spec

- [x] 3.1 Extend the `frontend-architecture` dashboard-delivery requirement with MIME-type correctness independent of the OS registry

# Tasks

## 1. Spec and discovery gates
- [x] Create OpenSpec change.
- [x] Document built-in fingerprint baseline in `fingerprint.md`.
- [x] Document websocket parity gate in `ws-parity.md`.

## 2. Core foundation
- [x] Add proxy pool/binding/default policy/request-log schema and migration.
- [x] Implement strict route resolver with same-pool fallback candidates.
- [x] Add Codex upstream client seam requiring a resolved route and built-in fingerprint.

## 3. Surface migration
- [x] Migrate Responses HTTP/SSE and non-streaming paths.
- [x] Migrate Responses websocket/bridge paths.
- [x] Migrate compact, thread/goal, and Codex control paths behind a resolved route seam.
- [x] Add Codex upstream client route seam for model discovery and usage refresh clients.
- [x] Migrate files, transcription, usage/model paths.
- [x] Migrate token/OAuth paths.

## 4. Control plane and verification
- [x] Add admin/dashboard pool and account-binding controls.
- [x] Add route coverage tests for migrated compact/thread/goal/Codex control surfaces.
- [x] Add route coverage tests for Responses HTTP/SSE, Responses websocket, files, transcription, model, usage, and logging surfaces.
- [x] Run final cleanup/review gate before completing ultragoal.

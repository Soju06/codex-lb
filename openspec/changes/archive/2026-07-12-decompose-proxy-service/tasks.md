## 1. Implementation

- [x] 1.1 Create `openspec/changes/decompose-proxy-service/` proposal and tasks.
- [x] 1.2 Establish `_service/` as the private implementation package for `ProxyService` decomposition.
- [x] 1.3 Extract bottom-section private support types and low-dependency support helpers into `app/modules/proxy/_service/support.py`.
- [x] 1.4 Keep `app/modules/proxy/_support.py` as a compatibility shim for moved support names.
- [x] 1.5 Extract warmup data structures and methods into `app/modules/proxy/_service/warmup.py`.
- [x] 1.6 Keep `app/modules/proxy/_warmup.py` as a compatibility shim for moved warmup names.
- [x] 1.7 Keep `ProxyService` public imports and behavior unchanged.
- [x] 1.8 Lock the proxy decomposition target architecture in `DECISIONS.md`.
- [x] 1.9 Add proxy architecture fitness ratchets via `scripts/check_proxy_architecture.py` and `make architecture-check`.
- [x] 1.10 Extract HTTP bridge helpers and session lifecycle methods into a private proxy package.
- [x] 1.11 Extract WebSocket proxy methods and state helpers into a private proxy package.
- [x] 1.12 Extract streaming retry methods into a private proxy package.
- [x] 1.13 Extract smaller cohesive domains: compact, file ops, codex control, transcribe, request logging, API-key usage, rate-limit payloads, observability, and response-create helpers.
  - [x] 1.13.a Extract request logging into `app/modules/proxy/_service/request_log.py`.
  - [x] 1.13.b Extract API-key usage reservation, heartbeat, settlement, and cleanup into `app/modules/proxy/_service/api_key_usage.py`.
  - [x] 1.13.c Extract rate-limit headers, payloads, and usage refresh helpers into `app/modules/proxy/_service/rate_limit.py`.
  - [x] 1.13.d Extract file operations into `app/modules/proxy/_service/file_ops.py`.
  - [x] 1.13.e Extract transcription into `app/modules/proxy/_service/transcribe.py`.
  - [x] 1.13.f Extract Codex control into `app/modules/proxy/_service/codex_control.py`.
  - [x] 1.13.g Extract compact responses into `app/modules/proxy/_service/compact.py`.
  - [x] 1.13.h Extract observability and continuity logging helpers into `app/modules/proxy/_service/observability.py`.
  - [x] 1.13.i Extract response-create payload, size-guard, dump, image, and slimming helpers into `app/modules/proxy/_service/response_create.py`.
- [x] 1.14 Preserve strict pinned-file finalization fail-closed behavior from `fix-unary-refresh-connect-failover` while splitting file operations.

Future follow-up, outside this change: extract account selection, budget, and admission into `app/modules/proxy/_service/account_selection.py`.

## 2. Verification

- [x] 2.1 Run import smoke for `app.modules.proxy.service.ProxyService`.
- [x] 2.2 Run focused proxy tests.
- [x] 2.3 Run `uv run ruff check` on touched Python files.
- [x] 2.4 Run `uv run ty check` on touched Python files.
- [x] 2.5 Run OpenSpec validation.
- [x] 2.6 Review diff for accidental behavior changes beyond code movement.

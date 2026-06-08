## Why

`app/modules/proxy/service.py` has grown to **18,353 lines** with 420 symbols
in a single `ProxyService` class. The file is unnavigable, slows IDE indexing,
produces noisy diffs, and hinders parallel feature work because every proxy PR
touches the same file.

## What Changes

Extract domain-cohesive method groups from `ProxyService` into a focused private
implementation package while keeping `app.modules.proxy.service.ProxyService` as
the stable public façade.

The scalable layout is:

```text
app/modules/proxy/
├── service.py              # public façade / compatibility import surface
├── _support.py             # compatibility shim for older private imports
├── _warmup.py              # compatibility shim for older private imports
└── _service/               # private implementation package
    ├── __init__.py
    ├── support.py          # shared private state/exceptions/helpers
    ├── warmup.py           # warmup domain mixin + data structures
    ├── compact.py          # compact responses domain
    ├── file_ops.py         # file create/finalize/proxy domain
    ├── codex_control.py    # Codex control domain
    ├── transcribe.py       # audio transcription domain
    ├── request_log.py      # request-log persistence domain
    ├── api_key_usage.py    # API-key reservation/settlement domain
    ├── rate_limit.py       # rate-limit payload/header domain
    ├── observability.py    # logging, hashing, continuity metrics helpers
    ├── response_create.py  # response.create payload shaping, slimming, dumps
    ├── http_bridge/        # future: session lifecycle, relay, reconnect
    ├── websocket/          # future: WS connect/relay/request state
    └── streaming/          # future: stream retry/settlement paths
```

Design patterns introduced:

- **Façade**: `service.py` remains the stable import surface for callers
- **Domain mixins**: each extracted domain owns its public methods and private helpers
- **Private package boundary**: implementation parts live under `_service/` instead of adding more flat modules
- **Compatibility shims**: old private module paths re-export moved names during incremental migration
- **Protocol-typed cross-mixin access**: mixins define narrow `Protocol`s for the `ProxyService` state/methods they use so `ty` can validate decomposition safely
- **Architecture fitness functions**: CI-visible ratchets keep `service.py`, shims, façade re-exports, and `_service/` boundaries from regressing

Target modules / packages:

| New module/package | Responsibility |
|---|---|
| `_service/support.py` | Shared private support exceptions, state dataclasses, and low-dependency support helpers |
| `_service/warmup.py` | Warmup lifecycle, account snapshot, request submission |
| `_service/http_bridge/` | HTTP bridge session lifecycle, submit, relay, retry, reconnect |
| `_service/websocket/` | WebSocket proxy, relay, upstream message processing |
| `_service/streaming/` | `_stream_with_retry`, `_stream_once`, retry helpers |
| `_service/compact.py` | `compact_responses` and related helpers |
| `_service/file_ops.py` | File create/finalize/proxy, file-pin cache |
| `_service/codex_control.py` | `codex_control_request` |
| `_service/transcribe.py` | `transcribe` |
| `_service/request_log.py` | Request-log write/persist/track |
| `_service/api_key_usage.py` | Reservation heartbeat, settlement, release |
| `_service/rate_limit.py` | Rate-limit headers, payload, additional limits |
| `_service/observability.py` | Proxy request shape/payload logging, continuity logging, identifier hashing helpers |
| `_service/response_create.py` | Response-create payload text, size guards, image detection, slimming, dump metadata |
| `_service/account_selection.py` | Future follow-up: account selection, budget, admission |

After this extraction wave, `service.py` retains only:

- `ProxyService.__init__` and composition of private mixins
- shared constants/helpers that genuinely cross domains and are not ready to move
- import re-exports so external callers remain unchanged

## Impact

- **Primarily behavior-preserving code movement.** During review this PR also
  preserves the existing `fix-unary-refresh-connect-failover` invariant that
  strict account-owner requests, including pinned file finalization, fail closed
  instead of falling back to another account after repeated 401s.
- External imports (`from app.modules.proxy.service import ProxyService`) continue
  to work without modification.
- Existing private imports from `app.modules.proxy._support` and
  `app.modules.proxy._warmup` continue through shims while follow-up PRs migrate
  internal callers to `_service.*`.
- Future PRs touching proxy get narrower diffs and clearer ownership boundaries.
- Proxy architecture regressions become visible during `make lint` instead of being discovered by review after the façade grows again.

## Approach

Use domain mixins (for example, `_HTTPBridgeMixin`) that `ProxyService` inherits.
Each mixin lives in the `_service/` private implementation package. Shared state
is accessed via `self` at runtime, but type-checked through narrow `Protocol`s so
`ty` does not lose knowledge of `ProxyService` attributes after method extraction.

Use folder depth when a domain has multiple sub-responsibilities or is larger than
a small leaf module. For example, HTTP bridge and WebSocket should become folders
before moving thousands of lines, while compact/file/rate-limit can start as leaf
modules.

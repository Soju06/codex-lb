# Context: proxy-architecture

Normative requirements live in [`spec.md`](./spec.md). This document carries
free-form context for the proxy-architecture capability: architecture decision
records (ADRs) are appended here and superseded by later entries rather than
edited in place. Relocated from the former repository-root `DECISIONS.md`.

## ADR-0001: ProxyService target-architecture cutover refactor

- **Date:** 2026-06-05
- **Status:** Accepted
- **Scope:** `app/modules/proxy/service.py` and proxy runtime internals

### Context

`app/modules/proxy/service.py` grew into a large god object that mixed HTTP
bridge orchestration, WebSocket proxying, streaming retry and settlement,
request logging, API-key usage, file operations, compact responses, warmup,
rate-limit payloads, and observability helpers in one `ProxyService` class.

The service is production-critical and contains compatibility behavior that is
hard to safely rewrite in one pass. A big-bang replacement would create high
review risk, hidden behavior drift, and poor rollback properties. Random small
file-shaving also fails to create durable boundaries and lets the façade grow
again during later feature work.

### Decision

Use a **target-architecture cutover refactor** for proxy service decomposition.

`app/modules/proxy/service.py` remains the stable public façade and compatibility
import surface. Extracted implementation lives behind the private package
`app/modules/proxy/_service/`.

Small cohesive domains are leaf modules under `_service/`. Large domains are
folder packages with their own internal structure:

```text
app/modules/proxy/
├── service.py                    # public façade / compatibility surface
├── _support.py                   # compatibility shim
├── _warmup.py                    # compatibility shim
└── _service/                     # private implementation package
    ├── support.py
    ├── warmup.py
    ├── api_key_usage.py
    ├── request_log.py
    ├── rate_limit.py
    ├── file_ops.py
    ├── transcribe.py
    ├── codex_control.py
    ├── compact.py
    ├── observability.py
    ├── http_bridge/              # future: session lifecycle, relay, retry
    ├── websocket/                # future: connect, request state, replay
    ├── streaming/                # future: retry, once, settlement
    └── account_selection/        # future: budget, failover, admission
```

For high-risk future domains such as HTTP bridge, WebSocket, streaming, and
account selection, use this sequence:

1. Add characterization/golden-master tests for the current behavior
2. Use Mikado-style discovery to map hidden couplings and prerequisites
3. Introduce narrow protocols/adapters where needed, following Branch by
   Abstraction inside the monolith
4. Build the target domain package behind the façade
5. Switch `ProxyService` to the new package in one controlled cutover
6. Keep the codebase releasable at each commit

Architecture fitness functions are part of the decision. They ratchet the
current decomposition direction and prevent accidental regression:

- `service.py` must stay below the accepted line-count threshold
- `ProxyService` maximum method span must not grow beyond the accepted threshold
- `_support.py` and `_warmup.py` must remain compatibility shims only
- `service.py` must preserve required façade re-exports for existing consumers
- `_service/*` modules must not form arbitrary cross-domain dependencies

### Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| Big-bang rewrite of `ProxyService` | Too much behavior would move at once; difficult to review, test, and roll back |
| Continue small ad-hoc extraction PRs | Reduces line count but does not create durable domain ownership or prevent regression |
| Split into external microservices now | Deployment/runtime complexity is unnecessary for the current problem; this is an internal modularity issue first |
| Keep `service.py` as the implementation owner indefinitely | Preserves compatibility but leaves the god-object failure mode in place |

### Consequences

- Future proxy work should add new domain behavior under `_service/`, not directly
  into `service.py`
- Compatibility shims may remain while internal consumers migrate, but they must
  not gain new behavior
- Large domain migrations should prefer complete package cutovers with targeted
  characterization tests over shallow helper movement
- CI can enforce architectural ratchets through `scripts/check_proxy_architecture.py`
- The accepted thresholds are starting ratchets, not final goals; future PRs
  should lower them after major domain packages are extracted

## ADR-0002: OpenSpec-owned architecture ratchets

- **Date:** 2026-08-23
- **Status:** Accepted
- **Scope:** `openspec/specs/proxy-architecture/spec.md` and the required
  architecture lint checker

### Context

The architecture checker originally duplicated numeric limits in Python. Later
changes raised those constants while the normative OpenSpec values remained
unchanged, allowing the required lint gate to approve a tree that violated its
contract. A comparison test between two hard-coded copies would detect some
drift but would still leave two editable sources of truth.

### Decision

The normative fitness-gate requirement owns one marked TOML block containing
all numeric ratchets enforced by the checker. The checker loads that block once
per run with the Python standard library, validates the exact key set and
positive integer values, and passes those values directly to the limit checks.
It keeps no numeric ratchet copies in Python source.

The parser fails closed when the block is missing, duplicated, malformed,
incomplete, or contains unknown or invalid values. A definition failure skips
only checks that need the limits; independent AST, façade, package, shim, and
import-boundary checks continue so one spec error cannot hide unrelated
violations.

For example, lowering `load_balancer_lines` in the marked block immediately
changes the next checker run without a Python edit. If the current module then
exceeds the new value, the checker reports the observed count and the new
OpenSpec-owned limit and exits non-zero.

### Constraints and consequences

- Natural-language wording remains free to improve; only the stable markers,
  TOML fence, exact keys, and values form the machine-readable interface.
- Ratchet changes are normative OpenSpec changes and receive the same review as
  any other proxy architecture contract change.
- Invalid spec syntax blocks the required lint gate rather than silently
  reverting to defaults.
- The threshold block is intentionally visible beside the requirement so code
  review sees policy and implementation changes together.

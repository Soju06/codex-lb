## Context

The documented standalone `docker run` command attaches codex-lb to Docker's legacy `bridge` network. On Linux hosts using `systemd-resolved`, Docker can copy the currently active Wi-Fi resolver into that container's `/etc/resolv.conf`; an existing container may continue querying that private resolver after the host roams to another network. The observed runtime then produced repeated `socket.gaierror(EAI_AGAIN)` failures, failed over through unrelated accounts, and made continuity owners unavailable until process restart cleared transport state.

Compose deployments already receive a project-scoped user-defined bridge, whose containers use Docker's embedded resolver. The application also already has a lease-safe shared HTTP client rotation mechanism, bounded Responses request budgets, SSE keepalives, and upstream WebSocket reconnect paths. The change should compose those facilities rather than introduce a second retry or health subsystem.

## Goals / Non-Goals

**Goals:**

- Prevent the stock standalone Docker launch from binding DNS directly to one Wi-Fi network's resolver.
- Distinguish host/process DNS or route failures from account-specific upstream failures.
- Recover pre-visible Responses and upstream WebSocket connection attempts on the same account within the existing request deadline.
- Rotate stale shared HTTP connector/DNS state once per failed client generation and keep recovery observable.

**Non-Goals:**

- Guarantee replay after model output has already been exposed downstream.
- Route a `previous_response_id` or account-owned file to a different account.
- Bundle a public recursive resolver, bypass VPN/split-DNS policy, or require host networking.
- Keep a request alive beyond its configured proxy/request budget.

## Decisions

### Classify process-wide network failures from exception chains

A core helper will walk exception causes and classify only DNS resolver failures (`socket.gaierror` transient/unresolvable results) and local route failures such as `ENETDOWN`, `ENETUNREACH`, and `EHOSTUNREACH`. The resulting internal error code will be account-neutral. Connection resets, refused connections, TLS failures, proxy endpoint failures, and upstream HTTP statuses remain on their existing account/upstream paths.

Message matching is retained only for serialized stream events where Python exception identity cannot cross the SSE boundary. The marker vocabulary is centralized with the typed classifier.

Alternative considered: treat every `upstream_unavailable` as global. Rejected because it would hide genuinely account-, proxy-, or upstream-specific failures from health routing.

### Rotate shared HTTP state with compare-and-swap semantics

When the failing operation holds the current shared HTTP session, recovery rotates only if that session still belongs to the current client. Concurrent failures from the retired generation observe that another caller already rotated it and do not build more clients. WebSocket-only failures may request a cooldown-coalesced rotation so background OAuth, usage, and model calls also discard stale resolver/connector state.

Alternative considered: restart the process or container from a watchdog. Rejected because it terminates every local downstream session and makes recovery depend on an external restart policy.

### Retry only while replay is safe and within the existing deadline

Pre-visible Responses transport failures use the existing same-account loop but network-recovery attempts are not charged to the account retry limit. Upstream WebSocket opens perform the same bounded loop centrally so native WebSocket and HTTP bridge callers share behavior. Existing SSE keepalive injection and WebSocket transport pings keep the local client connection alive while the loop sleeps with capped backoff.

Once output is downstream-visible, existing fail-closed and replay-safety rules remain unchanged. Continuity owners stay pinned throughout recovery.

Alternative considered: fall back from WebSocket to HTTP on DNS failure. Rejected because both transports require the same name resolution and changing transport does not repair host connectivity.

### Use Docker embedded DNS rather than hard-coded public resolvers

Standalone examples create and attach to a named user-defined bridge. Compose files declare an explicit default bridge and receive a configuration test so the contract cannot regress. No public DNS IP is hard-coded; this preserves host, VPN, and enterprise resolver policy.

Alternative considered: set Compose `dns:` to public resolvers. Rejected because public DNS can be blocked and can bypass split-DNS or organization policy.

## Risks / Trade-offs

- [A long host outage keeps safe requests pending until their request budget expires] → Existing budgets and SSE/WebSocket keepalives bound resource retention and preserve operator control.
- [DNS error text differs across platforms after serialization] → Prefer typed exception-chain classification and cover the supported Linux/macOS resolver messages in the centralized fallback matcher.
- [A user-defined Docker network still depends on Docker daemon DNS forwarding] → The embedded resolver tracks the daemon/host path better than a copied legacy-bridge resolver, while the application recovery loop remains the second layer.
- [Concurrent failures trigger excessive client rotations] → Compare against the failed session/client generation and coalesce generation-less recovery requests by cooldown.

## Migration Plan

Existing Compose users receive the explicit user-defined network on normal recreate. Existing standalone users can create the named network and attach the running container without changing volumes or ports; future recreates should include `--network`. Rollback removes the network option and reverts the application recovery helper without data migration.

## Open Questions

None.

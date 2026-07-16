## Context

The documented standalone `docker run` command attaches codex-lb to Docker's legacy `bridge` network. On Linux hosts using `systemd-resolved`, Docker can copy the currently active Wi-Fi resolver into that container's `/etc/resolv.conf`; an existing container may continue querying that private resolver after the host switches to another network. The observed runtime then produced repeated `socket.gaierror(EAI_AGAIN)` failures, failed over through unrelated accounts, and made continuity owners unavailable until process restart cleared transport state.

Compose deployments already receive a project-scoped user-defined bridge, whose containers use Docker's embedded resolver. Live network-switching validation showed that this is not sufficient on every Linux host: the container continued querying `127.0.0.11`, but Docker retained the old Wi-Fi forwarding servers until the container restarted. A host-network test on the same machine used the live `127.0.0.53` systemd-resolved stub and resolved successfully. The application also already has a lease-safe shared HTTP client rotation mechanism, bounded Responses request budgets, SSE keepalives, and upstream WebSocket reconnect paths. The change should compose those facilities rather than introduce a second retry or health subsystem.

## Goals / Non-Goals

**Goals:**

- Prevent the portable standalone Docker launch from binding DNS directly to one Wi-Fi network's resolver without claiming that Docker's embedded forwarders always refresh.
- Provide a stock Linux launch option that shares a stable host resolver path across Wi-Fi changes, while documenting that host networking alone does not stabilize a direct DHCP resolver address.
- Distinguish host/process DNS or route failures from account-specific upstream failures.
- Recover proven pre-dispatch Responses work, token refresh, and upstream WebSocket connection attempts on the same account within the existing request deadline.
- Rotate stale shared HTTP connector/DNS state once per failed client generation and keep recovery observable.

**Non-Goals:**

- Guarantee replay after model output has already been exposed downstream.
- Route a `previous_response_id` or account-owned file to a different account.
- Bundle a public recursive resolver or bypass VPN/split-DNS policy.
- Keep a request alive beyond its configured proxy/request budget.

## Decisions

### Classify process-wide network failures from exception chains

A core helper will walk exception causes and classify DNS resolver failures and local route failures such as `ENETDOWN`, `ENETUNREACH`, and `EHOSTUNREACH`. The resulting internal error code will be account-neutral and survive credential-safe routed-error sanitization. For a configured proxy endpoint, transient DNS failure remains process-wide but permanent name-not-found remains endpoint-scoped, because a misspelled proxy hostname is not evidence that the laptop lost DNS. Connection resets, refused connections, TLS failures, other proxy endpoint failures, and upstream HTTP statuses remain on their existing account/upstream paths.

Arbitrary upstream or serialized message text is not accepted as local-network provenance. Routed errors preserve a credential-safe stable internal code before the original exception is sanitized; serialized terminal events retain that code for account-neutral settlement but are not replayed.

Alternative considered: treat every `upstream_unavailable` as global. Rejected because it would hide genuinely account-, proxy-, or upstream-specific failures from health routing.

### Rotate shared HTTP state with compare-and-swap semantics

When the failing operation holds the current shared HTTP session, recovery rotates only if that session still belongs to the current client. Concurrent failures from the retired generation observe that another caller already rotated it and do not build more clients. WebSocket-only failures may request a cooldown-coalesced rotation so background OAuth, usage, and model calls also discard stale resolver/connector state.

Alternative considered: restart the process or container from a watchdog. Rejected because it terminates every local downstream session and makes recovery depend on an external restart policy.

### Retry only while replay is safe and within the existing deadline

Responses transport failures use the existing same-account loop only when the failing layer proves request dispatch did not occur; network-recovery attempts are not charged to the account retry limit. Token-refresh network failures recover on the same account only when a typed connector failure proves the refresh POST was not dispatched; response and body-read failures remain account-neutral but do not replay a possibly consumed rotating token. Upstream WebSocket opens perform the same bounded loop centrally so native WebSocket and HTTP bridge callers share behavior. Client rotation, construction, cleanup, and backoff receive the original monotonic deadline and recompute it after awaited work. Existing SSE keepalive injection and WebSocket transport pings keep the local client connection alive while the loop sleeps with capped backoff.

Downstream visibility is not the replay boundary: a send or receive failure can happen after upstream dispatch but before any downstream output. Post-connect WebSocket failures and serialized terminal response events therefore surface account-neutrally without transparent replay. Continuity owners stay pinned throughout recovery.

Alternative considered: fall back from WebSocket to HTTP on DNS failure. Rejected because both transports require the same name resolution and changing transport does not repair host connectivity.

### Distinguish portable bridge mode from Linux network-switching mode

Portable standalone examples create and attach to a named user-defined bridge. Compose files declare an explicit default bridge and receive a configuration test so the contract cannot regress. The guidance explicitly states that Docker's embedded resolver may retain stale external forwarders. Linux operators who frequently switch Wi-Fi, hotspots, or VPNs receive an alternative `--network host` launch, verified on `systemd-resolved` to preserve the host's live `127.0.0.53` resolver path. The stable local resolver is a prerequisite because a direct DHCP resolver address can become stale in host-network mode too. No public DNS IP is hard-coded; this preserves host, VPN, and enterprise resolver policy.

Alternative considered: set Compose `dns:` to public resolvers. Rejected because public DNS can be blocked and can bypass split-DNS or organization policy.

Alternative considered: claim that a user-defined bridge alone fixes resolver behavior after switching networks. Rejected after live validation reproduced `EAI_AGAIN` for more than a minute while `127.0.0.11` retained the old Wi-Fi forwarder; restart immediately regenerated the new forwarder.

## Risks / Trade-offs

- [A long host outage keeps safe requests pending until their request budget expires] → Existing budgets and SSE/WebSocket keepalives bound resource retention and preserve operator control.
- [Credential-safe routed errors cannot expose their original exception text] → Preserve a stable internal classification before sanitization and never reconstruct provenance from arbitrary message text.
- [Cancellation interrupts shared-client replacement] → Close partial sessions/connectors under cancellation and publish a replacement only after construction succeeds.
- [A user-defined Docker network still depends on Docker daemon DNS forwarding] → Document that it is only the portable baseline; offer Linux host networking or a host-resolver bridge listener when reliable network switching matters.
- [The verified Docker Engine host-network path requires a stable host resolver, removes network-namespace isolation, and applies to Linux] → Keep it opt-in, document the resolver prerequisite and direct-DHCP limitation, omit incompatible port publishing, explain the trade-off next to the command, and avoid presenting Docker Desktop's distinct host-network implementation as a verified DNS-recovery guarantee.
- [Concurrent failures trigger excessive client rotations] → Compare against the failed session/client generation and coalesce generation-less recovery requests by cooldown.

## Migration Plan

Existing Compose users receive the explicit user-defined network on normal recreate. Portable standalone users can create the named network and attach the running container without changing volumes or ports, but this is not presented as a network-switching guarantee. Linux users can select host networking on a future recreate, or expose systemd-resolved on the existing Docker bridge and repoint a running container without restarting it. Rollback removes the network option or host resolver listener and reverts the application recovery helper without data migration.

## Open Questions

None.

# Codex Desktop authentication bridge

## Purpose

Codex Desktop signed in with ChatGPT keeps the built-in OpenAI provider and
sends `Authorization: Bearer <ChatGPT access token>` to `openai_base_url`.
codex-lb with API-key auth expects an LB key instead. The bridge is a small
loopback relay that checks the caller's bearer against the current access token
in the local `auth.json`, then swaps it for the LB key. It is related to public
issue #2262 and is an independent implementation; no community bridge code was reused.

## Decisions

- **Standalone process, not a server mode.** The bridge is a separate CLI
  subcommand with its own aiohttp app. It imports no settings, database, or
  `app.main`, so the main server and its routing are untouched and the feature
  is off unless started (PRINCIPLES P1/P2).
- **Possession of the current access token is the local credential.** Comparing
  the inbound bearer to the current `tokens.access_token` limits the bridge to
  callers that possess that token, including a caller with a copied token who
  cannot read `auth.json` directly. The file is re-read per request
  so Codex token refreshes keep working; the bridge never writes it.
  This does not make credential rotation seamless: a partial in-place write is
  refused with 503, and a client retaining the previous token receives 403 until
  it reloads the new credential. Restart Desktop if that mismatch persists.
- **Fail closed on other credential stores.** Codex can mint bearers from an
  agent identity or keep tokens only in an OS keyring. Those bearers do not
  match `auth.json`, so v1 answers 403. The bridge does not toggle client
  features to avoid this.
- **Never answer 401 locally, and mask upstream 401.** Codex treats HTTP 401
  from its model provider as a recoverable ChatGPT auth failure and may try to
  refresh or log out. Local denials are 403, credential outages are 503, and an
  upstream 401 (a bad LB key) becomes 502.
- **No redirects anywhere.** aiohttp requests use `allow_redirects=False`.
  `websockets` `connect()` follows redirects by default, so the bridge
  subclasses it and overrides the public `process_redirect` hook to return the
  exception unchanged. All 3xx become 502 so a `Location` never leaks.
- **Opaque bodies.** Server and client decompression are off, so Codex request
  compression and upstream response encodings pass byte-for-byte with their
  `Content-Encoding`. Body and message limits reuse
  `MAX_DECOMPRESSED_RESPONSES_BODY_BYTES` (128 MiB) from the ingress budget.
- **Fixed constants instead of knobs.** `MAX_ACTIVE_REQUESTS=16`,
  `CONNECT_TIMEOUT_SECONDS=10`, `IDLE_TIMEOUT_SECONDS=300`, and
  `MAX_LIFETIME_SECONDS=7200` live in `app/desktop_bridge/relay.py`.

## Constraints and failure modes

| Condition | Result |
|-----------|--------|
| Bad port, unusable credential at startup | `ValueError`, nothing bound |
| CONNECT/TRACE | 405 (authority-form CONNECT never matches a route: 404) |
| Absolute-form target, traversal, bad encoding, bad WS handshake | 400 |
| Path outside `/backend-api/codex` | 404 |
| Foreign/duplicate Host, any Origin, missing/duplicate/mismatched bearer | 403 |
| Credential file missing, partial, not private, or invalid at request time | 503 |
| Active-request limit reached | 503 immediately |
| Declared or streamed request body over 128 MiB | 413 |
| Upstream connect failure, timeout, 3xx, 401 | 502 |
| Upstream failure after response bytes were sent | downstream connection closed |

- Cookies are stripped in both directions. This is a deliberate v1 limit:
  codex-lb does not need them, and forwarding ChatGPT cookies to the LB would
  leak a second credential.
- `Expect` is handled hop-locally by the bridge's server and is not forwarded.
- Upstream HTTP connections are not reused (`force_close`), so a stale
  keep-alive socket can never fail or replay a non-idempotent request.
- Toward codex-lb the bridge adds no automatic headers. Toward the client,
  aiohttp's server fills in `Date` and `Server` when codex-lb omits them, and
  `Content-Type: application/octet-stream` for a non-empty untyped body.
- Permission checks need POSIX ownership and mode bits. On platforms without
  them (Windows), only the regular-file, size, and content checks apply.
- WebSocket negotiation uses only the upstream-selected subprotocol. If the
  upstream selects none, the downstream handshake uses a cloned request without
  the offer, avoiding aiohttp's warning containing raw client values. A separate
  silent protocol logger also covers malformed HTTP before the handler runs.
- WebSocket keepalive pings are hop-local on each side. The idle limit counts
  only relayed data messages, so a session with no data in either direction for
  `IDLE_TIMEOUT_SECONDS` is closed with 1001 and Codex reconnects.
- Local acceptance with installed Desktop runtime `0.162.0-alpha.17.2` and
  CodexLB `1.24.0` completed model discovery, a normal WebSocket turn, and an
  ephemeral `experimentalPredictionMode` fork and turn with provider `openai`.
  The run used synthetic prompts in an isolated Codex home and removed its
  temporary credential copies afterward. Original configuration/auth were unchanged.
- A subsequent manual macOS test with Desktop `26.1007.30912` confirmed a
  visible Composer suggestion after about 10 seconds without leaving the
  thread, plus successful Tab acceptance. The timing is a single user-observed
  result, not a benchmark. Realtime, mobile, and Windows remain unverified.
  The bridge does not grant feature eligibility or account entitlements.

## Package lifecycle

The bridge is distributed through the existing CodexLB package, with no separate
self-updater. Operators update the official package and restart the bridge.
Forwarded catalogs and routes reduce hardcoded client coupling but cannot ensure
compatibility with future authentication changes. An optional external supervisor
can stage a specific stable release, run compatibility checks and keep the previous
complete environment for rollback. A host bridge and a containerized LB have
separate processes and installation environments.

Graceful process shutdown sends established WebSocket peers close code 1001.
Their closes run concurrently with a 10-second deadline, followed by aiohttp's
5-second HTTP completion window and 5-second cancellation window. Short HTTP
responses may finish; unfinished streams are disconnected without replay. A
late upstream WebSocket handshake is refused with 503 and its upstream closed.
The listener stops accepting immediately, but a restart is still a connection
interruption rather than a transfer of active model work to another process.
Allow an external supervisor at least 45 seconds before forced termination,
covering these phases, upstream close cleanup and scheduling overhead. These
deadlines rely on the supported transports honoring asyncio cancellation;
the supervisor remains the final bound for an unresponsive process.

Starting the bridge does not require the LB to be ready. Requests return 502
while its listener is unavailable; later requests recover in the same bridge
process when the LB starts. This supports a host login occurring before Docker
finishes startup without adding a request retry or a dependency polling loop.

The canonical spec is already synchronized for documentation links. After the
change is verified and merged, archive it with `--skip-specs` to avoid applying
the same added requirements a second time. Do not archive before verification.

## Example

Synthetic values only. Start codex-lb on its default port 2455 with API-key
auth, store an LB key in a private file (`chmod 600`), and start the bridge on
port 2456 with that file. Then point Codex at the bridge while keeping the
ChatGPT login:

```toml
# ~/.codex/config.toml
model_provider = "openai"
openai_base_url = "http://127.0.0.1:2456/backend-api/codex"
```

A Desktop request

```http
POST /backend-api/codex/responses HTTP/1.1
Host: 127.0.0.1:2456
Authorization: Bearer <access_token from ~/.codex/auth.json>
ChatGPT-Account-ID: 00000000-0000-0000-0000-000000000000
originator: codex_desktop
Content-Encoding: zstd
```

reaches codex-lb as

```http
POST /backend-api/codex/responses HTTP/1.1
Host: 127.0.0.1:2455
Authorization: Bearer <contents of the LB token file>
originator: codex_desktop
Content-Encoding: zstd
```

with the same compressed body bytes.

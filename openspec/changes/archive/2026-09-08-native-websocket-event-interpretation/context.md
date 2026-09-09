# Native Responses WebSocket event interpretation

## Ownership and legacy cleanup

Responses WebSocket calls opt into `websocket_responses_events_v1`. Rust
classifies JSON objects and embeds the original object in IPC with its original
text and type. The IPC decoder supplies the policy payload, avoiding a second
Python JSON parse. Rust strips only whitespace outside JSON strings in the
embedded object so pretty-printed JSON cannot split a JSON-line record; the
separate frame text stays byte-for-byte unchanged. Numeric tokens, duplicate keys,
Unicode and escapes remain opaque to numeric conversion.

A string `type` wins, including empty strings. Otherwise a dict `error` classifies
as `error`. Aliases remain unchanged at this WebSocket boundary. Unlike HTTP SSE,
the existing WebSocket relay does not normalize those aliases. Public errors and
HTTP-specific normalization stay at their existing Python consumers.

`NativeUpstreamWebSocket` carries the decoded payload to the actual WebSocket
relay and HTTP bridge. Request ids, sequence numbers, tool-call fields and usage
remain available for Python request matching and settlement. Lifecycle validation
still runs where the consumer uses those model fields. The bridge only bypasses
its parser for single-line objects beginning with `{`, preserving its legacy
SSE-field semantics for pretty-printed/whitespace-prefixed frames. Unsupported
JSON, surrogate keys/types and objects larger than 1 MiB keep opaque delivery.
The 1 MiB interpretation bound leaves space under the 24 MiB IPC line limit for
the embedded payload and escaped text/type; larger frames keep existing limits.
Queue accounting charges both serialized payload copies plus event-type metadata.

The old metadata branch in `_stream_codex_websocket_events` was removed: it
expected aiohttp-shaped messages, while the native adapter exposes kind/text.
It was not the consumer reached by the Responses WebSocket policy. Keeping that
branch gave a misleading impression of completed migration and emitted the wrong
SSE framing. The remaining Python parser serves actual Python transports.

## Where future fixes belong

| Concern | Owner | Python code still required |
| --- | --- | --- |
| Native HTTP SSE framing | `crates/codex-lb-egress/src/sse.rs` | Missing-helper transport fallback |
| Native compact collection | `crates/codex-lb-responses/src/compact.rs` | Python transport collection and public error mapping |
| Native HTTP event interpretation | `crates/codex-lb-responses/src/stream.rs::interpret` | Error context and unsupported JSON handoffs |
| Native WebSocket classification | `crates/codex-lb-responses/src/stream.rs::interpret_websocket` | Opaque/oversized frames and Python transports |
| Request matching, sequence, tool calls, errors, retry and settlement | Python WebSocket/bridge policy consumers | Active implementation, not retired legacy |

For each completed migration slice, compare Python changes since the previous
migration baseline, port applicable fixes, and remove superseded native-path
branches in the same change. Keep only the explicitly supported Python fallback
or policy owner, and extend the shared fixtures before removing its tests.

## Python-to-Rust parity audit (2026-09-08)

Reviewed the main history from native SSE framing (`8c6467d97`) through
`713fa5648`, including HTTP collection/interpretation migrations and newly merged
legacy cleanup PRs #2188 and #2191. Also incorporated #2186/#2192
(dashboard settings/transport ownership) and #2189 (unused ORM mappings). Those new Python changes remove unused modules,
retry helpers, proxy functions and bridge shims; they add no event behavior to
backport. The main merge preserves these deletions and the dashboard-owned
transport decision before native dispatch; Rust does not read the removed env setting.

The pre-merge audit on 2026-09-09 extends through `d3f63331d`: #2198 merges
the existing migration heads, and #2200 corrects transport test fixtures.
PRs #2187, #2190 and #2185 centralize environment reads and retire or constrain
settings. The transport-policy cleanup remains in Python before dispatch.
These changes leave the migrated event parsers and native protocol unchanged;
merging main preserves these fixes and removals without adding another Rust
policy implementation.

The audit found corrections needed in this WebSocket slice: preserve original
text and numeric values; use last-key precedence and typeless-error classification;
retain alias behavior of each actual consumer; carry complete payloads for request
matching; and consume metadata at the WebSocket relay and HTTP bridge. These cases
are pinned in `crates/codex-lb-responses/tests/fixtures/websocket-v1.json`, used by
Rust tests and `tests/integration/test_native_websocket_events.py` through the real
helper. Parser-rejection checks prove payload reuse in both Python consumers.

## Verification and performance

The tracked reproduction is `scripts/bench_native_websocket.py`; the measured
result is `benchmark.json` alongside this document. Run from the repository root:

```sh
CODEX_LB_NATIVE_EGRESS_TEST_BINARY=/path/to/codex-lb-native-egress uv run python scripts/bench_native_websocket.py
```

Same release helper binary, loopback WebSocket, 2,048 canonical delta frames,
3 warmups and 12 samples per mode. Median elapsed/helper CPU milliseconds were
`640 / 310` raw and `674 / 330` interpreted. This shared-host synthetic run shows
no speedup; it does not measure complete request-policy processing or production
throughput. Payload duplication in IPC is a cost of retaining original text while
removing the second Python parse. Do not claim a performance gain from this slice.

Rust workspace tests, release helper wire probes, Python relay/bridge regression
suites, Ruff/type/architecture checks and strict OpenSpec validation are required
for the corrected head. Final results are recorded in the PR.

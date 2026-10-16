# Context: record-resolved-upstream-transport-label

Normative text lives in the delta `specs/responses-api-compat/spec.md`.

## Requirement history (it has not flipped back and forth; this is the first reversal)

| Date | Commit | Change |
|---|---|---|
| 2026-06-26 | `2e124df8f` (#1093) | Code: `else configured_transport` added to keep the `"auto"` mode for the 426 fallback |
| 2026-06-26 | `cd01609b3` (#1096) | Spec: "downstream HTTP sticky records preserved auto upstream mode", which asserts `upstream_transport` is `"auto"` |
| 2026-07-13 | `07c6babe4` (#1252) | Synced into the main spec unchanged |
| 2026-09-26 | this change | That scenario is replaced in place: record the resolved transport |

#1096's stated purpose was to let operators answer "did this request actually
use upstream HTTP, auto/WebSocket, or native WebSocket". Recording `"auto"`
fails that purpose. The superseding ask ("record resolved transport rather than
raw configured `auto`") is the newer, explicit user requirement, so the old
scenario is replaced rather than kept next to a new one.

The `Request Logs API returns upstream transport` scenario uses `"auto"` only as
an echoed persisted value, and historical rows still hold it. It does not
contradict this change and is left unchanged.

## Decision: separate recorded label from client mode

The upstream client needs `"auto"` in order to fall back on a 426. The log needs
the concrete transport. The fix keeps two values: the mode passed to the client,
unchanged, and the resolved transport, recorded. Before the policy branch
substitutes `configured_transport`, the resolved transport is already known as
`resolved_base_transport`.

## Known bound

When `"auto"` resolves to WebSocket and upstream rejects the handshake with 426,
the client switches to HTTP inside `app/core/clients/proxy.py`. The service
layer is not told, so such a request logs `"websocket"`. The client already logs
that event as `upstream_websocket_handshake_rejected … retrying_transport=http`.
Plumbing it back into the request log is a separate change if needed.

## Oracle

- `tests/integration/test_proxy_responses.py::test_bypassed_request_logs_resolved_upstream_transport`
  exercises the real `/v1/responses` route with the bridge enabled, an inline
  image bypass under `always_websocket`, and a sticky smart-policy raw case.
  Both assert that the upstream client still receives `"auto"` while the
  persisted request log and transport-decision counter record `"websocket"`.
  `tests/integration/test_http_promotion_accounting.py` continues to assert
  the unchanged client mode (`raw_calls[-1]["upstream_transport"] == "auto"`).
  Focused smoke: `pytest -q tests/integration/test_proxy_responses.py -k test_bypassed_request_logs_resolved_upstream_transport`.

The initial pre-edit test attempt could not load `tests/conftest.py` because the
checkout interpreter lacked `aiohttp_socks`; the existing source-checkout
environment also lacked `jwt`. An isolated `/tmp` environment with the project
dependencies ran the focused test after the edit: both cases passed. Thus a
failing-before test assertion was not observed; the original write sites
passed the raw `upstream_stream_transport` (including `"auto"`) to persistence
and the decision counter.

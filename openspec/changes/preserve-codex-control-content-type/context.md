# Native control request media-type fidelity

## Evidence

Request `12333565-f752-4d0f-8b3d-a3042f110a2c` from Codex Desktop 0.153.4
reached `/v1/alpha/search` on 2026-09-07 at 04:10:59 UTC. Account selection
succeeded, then upstream returned HTTP 400 `Unsupported content type` at
04:11:01 UTC. The request used account-bound egress.

With a native user agent and lowercase JSON content type, the deployed control
header construction produces both `content-type: application/json` and
`Content-Type: application/json`. The native egress worker appends both to the
wire header map. This is a reproducible defect consistent with the upstream
rejection; the original inbound headers were not retained in request logs.

## Decision and example

Use the existing case-insensitive replacement helper at the control-request
boundary. For example, `content-type: application/sdp` now stays as one SDP
header rather than becoming a lowercase JSON field plus a PascalCase SDP field.
The body is opaque and unchanged. The shared Responses JSON policy is unchanged.

## Constraints and failure modes

Header order matters for native fingerprinting. Keep the first field's position
while removing case variants. Missing media type with a body retains the existing
JSON default; missing media type without a body emits no Content-Type. Tests
must exercise actual control-header construction, because mocking the control
function itself misses this defect. Existing upstream error and account policy
remain outside this header correction.

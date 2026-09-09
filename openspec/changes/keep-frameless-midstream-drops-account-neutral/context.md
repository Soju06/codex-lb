## Decision

Tungstenite represents a peer TCP reset without a Close frame as
`ProtocolError::ResetWithoutClosingHandshake`. Despite the enum category, the
native helper emits this specific case as a terminal transport failure; actual
frame-protocol violations remain protocol failures. Neither classification
authorizes replay after output.

A terminal WebSocket message without an upstream-authored close frame is
transport evidence, not account evidence. This remains true after response
events have started. Request progress is still load-bearing for replay safety:
a request that exposed model or tool output remains execution-ambiguous and is
not redispatched.

| Ending | Output observed | Account health | Eventless signal | Replay |
| --- | --- | --- | --- | --- |
| No frame / `None` or `1006` | No | Neutral | Existing windowed signal | Existing pre-visible guards only |
| No frame / `None` or `1006` | Yes | Neutral | No | No |
| Upstream-authored close frame | Any | Existing behavior | No | Existing guards |
| Protocol-invalid nonterminal message | Any | Existing behavior | No | Existing guards |

Transport-ending provenance is used for account-health attribution only. This
change does not tighten upstream's replay gate for protocol errors: accepted,
output-free requests remain subject to the existing replay guards. Model/tool
output still blocks replay.

## Rollout

Non-container installations must rebuild the native helper together with the
Python update. The required `websocket_close_frame_provenance_v1` capability
distinguishes received empty Close frames from EOF. An incompatible helper
fails closed with `NativeEgressProtocolError` before dispatch; missing
capabilities do not silently fall back to Python transport.

Unless an existing bounded pre-created recovery succeeds, the current request
still receives one terminal `stream_incomplete` result. Durable operations that
emitted events or buffered model output remain acknowledged and incomplete
event spools remain non-replayable. This prevents account backoff without
creating a second upstream execution or duplicate downstream output.

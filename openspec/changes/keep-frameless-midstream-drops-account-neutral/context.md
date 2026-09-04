## Decision

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
| Protocol-invalid nonterminal message | Any | Existing behavior | No | No |

Unless an existing bounded pre-created recovery succeeds, the current request
still receives one terminal `stream_incomplete` result. Durable operations that
emitted events or buffered model output remain acknowledged and incomplete
event spools remain non-replayable. This prevents account backoff without
creating a second upstream execution or duplicate downstream output.

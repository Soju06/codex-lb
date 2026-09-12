The current complete-transcript recovery is deliberately gated on
`response_event_count == 0`, because replaying an acknowledged request can
duplicate model work or tool execution. A websocket close after partial text
output is a different operational failure: a client cannot reconnect the
in-flight upstream response, but the bridge may already have a durable chain
of completed turns from which a fresh root response can be constructed.

The new path must not treat partial output as a completed turn. It discards
the interrupted response and replays only the durable parent transcript plus
the interrupted request input. This can duplicate text and is intentionally
unsafe. Function calls, custom tool calls, pending tool outputs, malformed
output manifests, and missing durable operation ownership are excluded because
the proxy cannot determine whether executing them again is safe.
Hosted and side-effecting Responses items/events (including web/file search,
code interpreter, computer use, image generation, MCP, and tool-search
lifecycles) are treated the same way: seeing one makes the partial replay
manifest unknown and keeps recovery fail-closed.
The output-item classifier is an allowlist for proven-safe message, reasoning,
and compaction items; unfamiliar output types are also rejected so newly added
hosted tools cannot silently become replayable.
Replacement terminal frames are matched to the replay owner before response
identity rewriting, so a failed or incomplete replacement cannot leak a new
upstream response ID into the client-visible stream.
These guards apply only to the explicitly enabled unsafe partial-replay path;
ordinary streaming and safe recovery behavior remain unchanged.

The durable operation row is first marked failed and then rebound under the
same identity. The rebound write is the single-attempt fence; authorization is
set only after it succeeds and is cleared before the replacement send. A
second transport failure therefore cannot create an automatic replay loop.

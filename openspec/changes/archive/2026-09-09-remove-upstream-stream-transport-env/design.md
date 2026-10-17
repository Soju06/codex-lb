# Design

## One resolver

`configured_upstream_stream_transport(dashboard_settings)` in
`app/modules/proxy/_service/support.py` is the single place that turns the
dashboard row into `auto | http | websocket`. It maps the legacy `default`
sentinel (and any unknown value) to `auto` so a 5 s settings-cache snapshot
taken before the data migration ran behaves exactly like the migrated row.
The three former call sites (`streaming/retry.py`,
`proxy/api.py` websocket 426 gate, `http_bridge/streaming.py` bypass) call it
instead of re-implementing the sentinel fallback.

## Client layer

`app/core/clients/proxy.py` receives the operator's choice as
`upstream_stream_transport_override` from the proxy service
(`streaming/mixin.py` forwards the resolved value). The client's own base
value is the constant `auto`, the same value the removed environment field
defaulted to, so callers that never passed an override keep their behaviour.

## Migration

`20260908_000000_replace_upstream_stream_transport_default_sentinel`
rewrites `default` -> `auto` and sets the server default to `auto`.
Downgrade restores the server default only: `auto` is valid in the previous
schema, and the original sentinel state is not recoverable.

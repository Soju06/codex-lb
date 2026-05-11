## Live Finding

On 2026-05-11, `codex-lb-direct` was healthy on `/health/ready` and
`/backend-api/codex/health`, with no recent `database is locked` log hits. The
remaining application exception came from `/backend-api/codex/responses` during
websocket connect-failure reporting.

The stack ended in:

- `app/modules/proxy/service.py::_emit_websocket_connect_failure`
- `websocket.send_text(...)`
- `starlette.websockets.WebSocketDisconnect`

That means the proxy had already decided to send a client-visible connect
failure and had already run cleanup/persistence before the client disappeared.
The correct behavior is to keep those side effects and suppress only the
expected downstream disconnect from the final send.

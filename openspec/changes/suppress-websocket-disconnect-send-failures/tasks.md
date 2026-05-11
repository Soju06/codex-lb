## 1. Implementation

- [x] 1.1 Inspect live logs and confirm the ASGI exception path
- [x] 1.2 Suppress expected `WebSocketDisconnect` from the final connect-failure downstream send
- [x] 1.3 Preserve reservation release, response-create gate release, and request-log persistence
- [x] 1.4 Add focused unit coverage for the disconnect path

## 2. Verification

- [x] 2.1 Run focused unit tests for websocket connect-failure cleanup
- [x] 2.2 Run focused `ruff` and `ty` checks for touched files
- [x] 2.3 Run `openspec validate suppress-websocket-disconnect-send-failures --type change --strict`
- [x] 2.4 Deploy through `./update.sh`
- [x] 2.5 Verify live health and recent logs after deploy

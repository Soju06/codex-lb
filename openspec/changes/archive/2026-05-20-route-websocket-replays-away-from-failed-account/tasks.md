## 1. Websocket Replay Routing

- [x] 1.1 Add request-local tracking for accounts that failed replayable websocket requests.
- [x] 1.2 Record a transient stream error before replaying after an upstream websocket close or send failure.
- [x] 1.3 Exclude failed accounts from fresh replay reconnect selection.
- [x] 1.4 Preserve previous-response owner affinity when replaying anchored follow-up requests.

## 2. Verification

- [x] 2.1 Add unit coverage for fresh replay account exclusion.
- [x] 2.2 Add unit coverage that previous-response replay does not exclude the owner account.
- [x] 2.3 Run focused websocket replay and previous-response affinity tests.

## 1. Tracking

- [x] 1.1 Record async function/custom tool call identities from upstream events
- [x] 1.2 Complete pending async calls only on matching typed outputs
- [x] 1.3 Persist the synchronous subset of the durable pending-tool manifest while async calls are outstanding

## 2. Injection

- [x] 2.1 WebSocket interrupted-output injection excludes pending async ids
- [x] 2.2 HTTP-bridge interrupted-output injection excludes pending async ids
- [x] 2.3 Clear pending async state on account rebind / denied-anchor / durable rehydrate mismatch

## 3. Verification

- [x] 3.1 Unit: async call survives an intervening turn; delayed output is forwarded
- [x] 3.2 Integration: HTTP-bridge function_call and custom_tool_call variants
- [x] 3.3 Strict OpenSpec validation of this change
- [x] 3.4 Account-neutral replay and durable suffix matching accept settled async pairs
- [x] 3.5 Reject malformed async suffix items before manifest comparison, with failing-first unit and HTTP-route regressions and focused validation

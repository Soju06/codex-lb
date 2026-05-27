## 1. Implementation
- [x] 1.1 Add bridge-key strength and hard-owner forwarding in the HTTP responses bridge
- [x] 1.2 Add internal owner-forward relay and forwarded-request loop prevention
- [x] 1.3 Extend ring membership with endpoint resolution via advertised metadata
- [x] 1.4 Add owner-forward metrics and structured bridge events

## 2. Verification
- [x] 2.1 Add regression coverage for hard mismatch forwarding, soft locality rebind, and ring endpoint resolution
- [ ] 2.2 Run targeted unit/integration tests plus `openspec validate --specs`

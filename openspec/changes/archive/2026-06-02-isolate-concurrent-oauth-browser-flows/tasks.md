## 1. Spec
- [x] 1.1 Add a frontend-architecture delta for concurrent OAuth browser flows.

## 2. Implementation
- [x] 2.1 Add per-flow OAuth state isolation in the backend service.
- [x] 2.2 Thread the flow identifier through the dashboard OAuth API and frontend hook.
- [x] 2.3 Preserve callback matching by `state` token for overlapping browser sign-ins.

## 3. Validation
- [x] 3.1 Add or update regression coverage for overlapping browser OAuth flows.
- [x] 3.2 Run targeted OAuth backend and frontend tests.
- [ ] 3.3 Validate specs locally with `openspec validate --specs`.

## 1. Identity and account locality

- [x] 1.1 Parse process session and thread identity independently and derive source-separated opaque thread keys
- [x] 1.2 Route backend Responses and compact through bounded thread locality with process-preference seeding
- [x] 1.3 Preserve raw legacy Codex rows and all exact hard-owner precedence/conflict behavior

## 2. Transport continuity

- [x] 2.1 Scope direct WebSocket replay/tool continuity by thread and refresh active thread locality
- [x] 2.2 Scope HTTP bridge canonical lanes by thread while preserving exact-alias migration and forwarded-key behavior
- [x] 2.3 Route thread-goal operations from payload `threadId`

## 3. Regression evidence

- [x] 3.1 Cover identity parsing, source separation, process seeding, Responses/compact parity, and unchanged cache hints
- [x] 3.2 Cover sibling direct-WebSocket replay isolation and thread-goal account selection
- [x] 3.3 Cover sibling bridge isolation, exact legacy alias recovery, and no old-canonical fallback
- [x] 3.4 Run focused tests, Ruff, type checks, OpenSpec checks available in the checkout, and review the final diff

## 1. Backend authorization and redaction

- [x] 1.1 Add an explicit admin-only dashboard read dependency and apply it to all conversation-archive routes.
- [x] 1.2 Redact client IP, full User-Agent, conversation ID, and archive lookup ID from guest request-log responses while preserving admin responses and operational metrics.
- [x] 1.3 Exclude persisted client IP from guest request-log search while preserving admin client-IP search.

## 2. Dashboard presentation

- [x] 2.1 Hide identifying request-detail fields and the archive panel for guest principals while preserving the admin view.

## 3. Focused verification

- [x] 3.1 Add backend regression tests for guest archive denial, guest metadata redaction, and unchanged admin data.
- [x] 3.2 Add frontend regression coverage for guest-hidden sensitive details and unchanged admin details.
- [x] 3.3 Run focused backend/frontend tests, lint or type checks for touched code, and strict OpenSpec validation.
- [x] 3.4 Add API regression coverage proving guest client-IP search is blocked while admin search remains available.

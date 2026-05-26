# Tasks: add-request-log-cost-breakdown

## 1. Specs
- [ ] 1.1 Add request-log cost-breakdown requirements.
- [ ] 1.2 Validate OpenSpec changes.

## 2. Backend
- [ ] 2.1 Add reusable request-log pricing breakdown helpers.
- [ ] 2.2 Expose `inputTokens`, `outputTokens`, and `costBreakdown` from `/api/request-logs`, including `reasoning_tokens` fallback for `outputTokens` and `null` placeholders for unavailable legacy breakdown segments.

## 3. Frontend
- [ ] 3.1 Extend the dashboard request-log schema for the new payload.
- [ ] 3.2 Render the `Cost` section in the request detail dialog only for `ok` rows.

## 4. Tests
- [ ] 4.1 Add backend coverage for request-log breakdown payloads.
- [ ] 4.2 Add frontend schema and dialog rendering coverage.

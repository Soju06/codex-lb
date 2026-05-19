## 1. Reservation Budgeting

- [x] 1.1 Add a typed request-usage reservation budget to API-key limit enforcement.
- [x] 1.2 Replace unconditional 8192/8192 token cost reservations with input/output budget-aware reservations.
- [x] 1.3 Preserve final usage settlement and release semantics.

## 2. Proxy Integration

- [x] 2.1 Estimate self-contained Responses/compact request input size from serialized payload bytes.
- [x] 2.2 Fall back to conservative input reservation for previous-response, conversation, file, or image references.
- [x] 2.3 Use a bounded default output reservation and avoid trusting output caps that codex-lb does not enforce upstream.
- [x] 2.4 Pass the estimated budget through HTTP and websocket reservation paths.

## 3. Tests and Validation

- [x] 3.1 Add regression coverage for eight simultaneous priority-lane reservations under a $5 cost limit.
- [x] 3.2 Add coverage for unsupported output caps, zero-reservation finalization, and opaque-input fallback.
- [x] 3.3 Run focused tests for API-key service and proxy usage-budget helper.
- [x] 3.4 Run `uv run ruff check` and `openspec validate --specs`.

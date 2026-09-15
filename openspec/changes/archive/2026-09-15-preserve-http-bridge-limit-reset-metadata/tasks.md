## 1. Reproduce

- [x] 1.1 Add a failing assertion that deferred pre-created health retains both reset fields.
- [x] 1.2 Add an HTTP-route regression using real account selection and persistence for an exhausted hard owner and a healthy replacement.

## 2. Implement

- [x] 2.1 Preserve parsed reset metadata in all three HTTP bridge retry health branches using the existing error conversion.
- [x] 2.2 Cover absolute/relative metadata and existing metadata-free and ownership boundaries without changing replay eligibility or settlement ordering.
- [x] 2.3 Apply the shared conversion to direct WebSocket retry health and prove metadata preservation there.

## 3. Verify

- [x] 3.1 Run focused unit and HTTP bridge regressions.
- [x] 3.2 Run the relevant local CI targets, strict OpenSpec validation, and independent diff review.
- [x] 3.3 Synchronize the capability specification and context, then archive the verified change.

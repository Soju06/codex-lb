## 1. Spec

- [x] 1.1 Add the credit-backed account availability requirement.
- [x] 1.2 Add the dashboard account credit display requirement.

## 2. Implementation

- [x] 2.1 Preserve latest credit metadata while deriving account summaries and load-balancer state.
- [x] 2.2 Allow present, unlimited, or positive-balance credits to override quota-derived blocked status.
- [x] 2.3 Expose credit metadata in account summary responses.
- [x] 2.4 Render account Credits on dashboard account cards.

## 3. Verification

- [x] 3.1 Cover credit-backed quota/status behavior in backend unit tests.
- [x] 3.2 Cover account summary credit extraction in backend unit tests.
- [x] 3.3 Cover dashboard schema and account card rendering in frontend tests.
- [x] 3.4 Run strict OpenSpec validation and targeted tests.

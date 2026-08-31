## 1. Regression

- [x] 1.1 Add an exact-name Rust regression with a local gzip origin that asserts decoded response bytes and decoded-representation headers.
- [x] 1.2 Run the regression before implementation and capture the intended RED assertion.

## 2. Implementation

- [x] 2.1 Enable the minimal reqwest response-decoder support needed for the demonstrated gzip contract.
- [x] 2.2 Run the same exact regression and capture GREEN.

## 3. Verification

- [x] 3.1 Run Rust formatting, compiler/Clippy diagnostics, focused tests, affected workspace checks/build, and strict scoped OpenSpec validation.
- [x] 3.2 Exercise a local gzip origin through the real native bridge and verify the sentinel plus internally consistent body/header semantics.
- [x] 3.3 Review the final diff for scope, simplicity, and unchanged routing/replay/WebSocket behavior.

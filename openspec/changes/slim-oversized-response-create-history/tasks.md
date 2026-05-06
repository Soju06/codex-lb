## 1. Implementation

- [x] 1.1 Add a second slimming pass that omits oldest historical input items
      after image/tool-output slimming if the request is still over budget.
- [x] 1.2 Preserve the recent user-turn suffix and insert an omission notice.
- [x] 1.3 Move oversized response-create dumps under the default app home
      directory.

## 2. Verification

- [x] 2.1 Add unit coverage for historical item omission.
- [x] 2.2 Add unit coverage for the portable dump directory default.
- [x] 2.3 Run targeted pytest, ruff, ty, and OpenSpec validation.

## 1. Implementation

- [x] 1.1 Bypass the writable `thread_header` sticky key in
      `preferred_owner_sticky_inputs` the same way as `session_header`.

## 2. Regression coverage

- [x] 2.1 Assert preferred-owner selection nulls the thread sticky key
      and keeps the process seed / raw legacy key.
- [x] 2.2 Assert an existing thread row is not upserted when a file
      pin is the required owner.

## 3. Validation

- [x] 3.1 Run the focused selection tests.
- [x] 3.2 Run strict OpenSpec validation for this change.

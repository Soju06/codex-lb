## 1. Implementation

- [x] 1.1 Grant `abandon_unavailable_legacy_owner` for `thread_header`
      when a process session is present and the payload is
      account-neutral.
- [x] 1.2 Allow retirement CAS when request source is `thread_header`.
      Keep the write scoped to `session_header`.
- [x] 1.3 Load the raw `legacy_sticky_key` as `session_header`
      interpretation so a scoped tombstone hides it from later
      thread-id turns.

## 2. Regression coverage

- [x] 2.1 Assert session-id + thread-id goal restart sets the
      abandonment flag; turn-state and account-dependent payloads do
      not.
- [x] 2.2 Assert sticky selection retires the raw owner and selects a
      replacement when source is `thread_header`.

## 3. Validation

- [x] 3.1 Run the focused affinity and sticky-selection tests.
- [x] 3.2 Run strict OpenSpec validation for this change.

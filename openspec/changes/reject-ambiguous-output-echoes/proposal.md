## Why

An unanchored continuation may echo the same persisted output more than once.
Choosing the first interior match silently guesses the replay ordering.

## What Changes

- Require a unique interior output match before removing an echoed subsequence.
- Reject ambiguous reconstruction before rebinding or dispatching a recovery.
- Protect the transcript builder and actual bridge recovery handler with regressions.

## Implementation

- [x] Move `claim_session`'s existing-row read and decision inside the SQLite
  writer section.
- [x] Guard the claim update with the observed owner and epoch.
- [x] Add a production-config SQLite concurrent-claim regression.

## Verification

- [x] Run strict OpenSpec validation for this change.
- [x] Run the durable bridge and HTTP bridge suites.

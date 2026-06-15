## Specification

- [x] 1.1 Create the OpenSpec proposal, context, and delta specs for reset-credit tracking.
- [x] 1.2 Keep consume and redemption workflow details explicitly out of scope for this change.

## Implementation

- [x] 2.1 Add the reset-credit client and payload models.
- [x] 2.2 Add the ORM model, migration, and repository queries.
- [x] 2.3 Refresh stored reset credits from the existing 60-second background refresh loop.
- [x] 2.4 Expose `availableResetCount` in shared account summary payloads.
- [x] 2.5 Add Accounts page, dashboard, and header UI counts, badges, and sort behavior.

## Verification

- [x] 3.1 Verify migration coverage for table shape, uniqueness, and indexes.
- [x] 3.2 Verify backend tests for fetch, persistence, expiry normalization, and shared payload counts.
- [x] 3.3 Verify frontend tests for schema parsing, sort behavior, badges, and disabled reset controls.
- [x] 3.4 Run `openspec validate --specs`.

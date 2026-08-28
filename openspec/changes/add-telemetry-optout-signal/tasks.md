## 1. Snapshot Consent Contract

- [x] 1.1 Add the active consent literal to snapshot schemas and introduce the typed opt-out event schema
- [x] 1.2 Require callers to pass resolved consent into snapshot construction and expose it in sender and preview envelopes
- [x] 1.3 Update schema allowlist and builder, scheduler, and preview regression tests for the consent field

## 2. Opt-Out Delivery

- [x] 2.1 Implement signed canonical opt-out delivery with lazy registration, activation, bounded retry, and debug-only failure isolation
- [x] 2.2 Detect dashboard effective active-to-inactive transitions and schedule one resource-owning background send
- [x] 2.3 Add sender and settings API tests for successful delivery, retry/failure isolation, repeated transitions, no-op decisions, and both environment overrides
- [x] 2.4 Preserve transport-level zero-call coverage for disabled scheduler and sender paths

## 3. Operator Communication

- [x] 3.1 Add neutral opt-out notice copy to the telemetry consent dialog and settings components with co-located test coverage
- [x] 3.2 Document the snapshot consent field, opt-out wire payload, transition behavior, and environment-path silence

## 4. Verification

- [x] 4.1 Validate the OpenSpec change and run focused backend and frontend tests
- [x] 4.2 Run the full unit suite, lint, and type-check gates and confirm the final diff stays within the approved scope

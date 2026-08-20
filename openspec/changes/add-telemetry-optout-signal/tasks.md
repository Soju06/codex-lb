## 1. Snapshot Consent Contract

- [ ] 1.1 Add the active consent literal to snapshot schemas and introduce the typed opt-out event schema
- [ ] 1.2 Require callers to pass resolved consent into snapshot construction and expose it in sender and preview envelopes
- [ ] 1.3 Update schema allowlist and builder, scheduler, and preview regression tests for the consent field

## 2. Opt-Out Delivery

- [ ] 2.1 Implement signed canonical opt-out delivery with lazy registration, activation, bounded retry, and debug-only failure isolation
- [ ] 2.2 Detect dashboard effective active-to-inactive transitions and schedule one resource-owning background send
- [ ] 2.3 Add sender and settings API tests for successful delivery, retry/failure isolation, repeated transitions, no-op decisions, and both environment overrides
- [ ] 2.4 Preserve transport-level zero-call coverage for disabled scheduler and sender paths

## 3. Operator Communication

- [ ] 3.1 Add neutral opt-out notice copy to the telemetry consent dialog and settings components with co-located test coverage
- [ ] 3.2 Document the snapshot consent field, opt-out wire payload, transition behavior, and environment-path silence

## 4. Verification

- [ ] 4.1 Validate the OpenSpec change and run focused backend and frontend tests
- [ ] 4.2 Run the full unit suite, lint, and type-check gates and confirm the final diff stays within the approved scope

## 1. Regression coverage

- [ ] Add an edit-dialog integration test that reproduces dropdown selection dismissing the parent dialog.
- [ ] Run the focused test and confirm it fails for the expected dismissal behavior.

## 2. Implementation

- [ ] Add the smallest shared guard needed by the API-key create and edit dialogs.
- [ ] Preserve ordinary outside-click dismissal and existing keyboard behavior.

## 3. Verification

- [ ] Run focused component and integration tests.
- [ ] Run frontend typecheck, lint, and build checks.
- [ ] Validate OpenSpec artifacts.

## 4. Live deployment

- [ ] Capture the current container runtime configuration and build a patched 1.20.1 image.
- [ ] Replace the live container without changing the persistent data volume.
- [ ] Verify health, API-key data, and the original multi-model GUI save flow.

## 5. Upstream handoff

- [ ] Commit the focused patch and push the branch.
- [ ] Open an upstream-ready PR that reports the live reproduction and verification evidence.

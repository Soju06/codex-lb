## 1. Regression coverage

- [x] Add an edit-dialog integration test that reproduces dropdown selection dismissing the parent dialog.
- [x] Run the focused test and confirm it fails for the expected dismissal behavior.

## 2. Implementation

- [x] Add the smallest shared guard needed by the API-key create and edit dialogs.
- [x] Preserve ordinary outside-click dismissal and existing keyboard behavior.

## 3. Verification

- [x] Run focused component and integration tests.
- [x] Run frontend typecheck, lint, and build checks.
- [x] Validate OpenSpec artifacts.

## 4. Live deployment

- [x] Capture the current container runtime configuration and build a patched 1.20.1 image.
- [x] Replace the live container without changing the persistent data volume.
- [x] Verify health, API-key data, and the original multi-model GUI save flow.

## 5. Upstream handoff

- [x] Commit the focused patch and push the branch.
- [x] Open an upstream-ready PR that reports the live reproduction and verification evidence.

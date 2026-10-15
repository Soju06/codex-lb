## 1. Complete unconditional HTTP preparation

- [x] 1.1 Extend the real-origin preparation regression with an auto image-generation request below the byte budget; verify it fails with one unused encode before the production fix.
- [x] 1.2 Reuse the image-tool predicate and skip only the auto image-generation size encode; verify the new case and existing preparation, transport and fallback tests pass.

## 2. Validate the contract

- [x] 2.1 Sync the explicit scenario and stable context into the owning capability; verify strict OpenSpec validation passes.
- [x] 2.2 Run applicable lint, type, build and regression gates, then verify the completed implementation and archive this change.

## 1. Spec
- [x] 1.1 Add an `api-keys` delta that describes the local-only behavior when API key auth is disabled.

## 2. Implementation
- [x] 2.1 Update the dashboard API key auth toggle copy to reflect the shared proxy route scope and local-only anonymous access.
- [x] 2.2 Update the README API key auth guidance and provider notes to match the current behavior.
- [x] 2.3 Update the main `openspec/specs/api-keys/spec.md` wording so the normative text matches the implementation.

## 3. Validation
- [x] 3.1 Review the changed copy for consistency across the dashboard, README, and OpenSpec.
- [ ] 3.2 Validate specs locally with `openspec validate --specs` if the CLI is available in the environment.

## ADDED Requirements

### Requirement: Publish compatibility support matrix
The project MUST maintain a support matrix in `refs/openai-compat-test-plan.md` that lists supported and explicitly unsupported OpenAI-compatible features for Responses and Chat. The matrix MUST be updated whenever behavior changes.

#### Scenario: Support matrix present
- **WHEN** the compatibility plan is reviewed
- **THEN** the document includes a table of supported and unsupported features for Responses and Chat

### Requirement: Live compatibility check output
The live compatibility check script MUST print the expected unsupported feature list and MUST write a results JSON file to `refs/openai-compat-live-results.json`.

#### Scenario: Live check run
- **WHEN** `scripts/openai_compat_live_check.py` is executed
- **THEN** the console output includes an expected unsupported list and the JSON results file is written

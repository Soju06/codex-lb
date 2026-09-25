# Tasks

## 1. Force Probe request contract

- [x] 1.1 Remove the unsupported output-token field from the account probe and update its request-body unit test; verify `tests/unit/test_accounts_service_probe.py` passes.
- [x] 1.2 Add a dashboard probe route test whose upstream stub rejects `max_output_tokens`; verify `tests/integration/test_accounts_api_probe.py` passes with a successful `probeStatusCode`.
- [x] 1.3 Sync the Force Probe requirement and its rationale into the main usage-refresh-policy spec and context; verify the change and main specs validate strictly.

## 2. Integration checks

- [x] 2.1 Run relevant lint, type, build, and test checks; verify each command passes and review the final diff for unrelated changes.

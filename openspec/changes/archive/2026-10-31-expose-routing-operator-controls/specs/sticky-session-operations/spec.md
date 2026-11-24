## ADDED Requirements

### Requirement: Operator may reassign only soft prompt-cache affinity

The dashboard MUST expose each visible sticky mapping's stable account ID.
An authenticated dashboard operator with write access MAY select a different
account for a non-reserved `prompt_cache` mapping. Reassignment MUST compare
the expected current account ID atomically and return a conflict if another
writer changed or deleted it. Missing, deleting, paused, deactivated,
rate-limited, quota-exceeded, or known-unroutable reauthentication targets MUST
be rejected. A preserved target MUST be rejected as contrary to operator policy.
The operation MUST reject other kinds and reserved ownership namespaces. It
MUST NOT alter any hard continuation mapping, live bridge, credential, or
in-flight request. Normal model, API-key, quota and ownership checks MUST still
apply to the next request; this operation is a soft preference, not an override.

#### Scenario: Soft mapping moves to a selected account

- **GIVEN** a non-reserved prompt-cache mapping owned by A
- **WHEN** an operator selects eligible B with expected owner A
- **THEN** the mapping is atomically updated to B
- **AND** hard continuation ownership remains unchanged

#### Scenario: Concurrent owner change is preserved

- **GIVEN** an operator viewed owner A but another request changed it to C
- **WHEN** the operator attempts reassignment with expected owner A
- **THEN** the API returns HTTP 409 and retains C

#### Scenario: Hard or unavailable selection is rejected

- **WHEN** reassignment targets a hard/reserved mapping or unavailable account
- **THEN** the API rejects it without changing mappings or credentials

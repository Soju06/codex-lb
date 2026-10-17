## ADDED Requirements

### Requirement: API keys can restrict client-selected reasoning efforts

The dashboard API-key create, update, list, and response surfaces SHALL expose
an optional `allowedReasoningEfforts` list. When absent or `null`, the API key
MUST retain unrestricted reasoning-effort behavior. When present, the list
MUST be non-empty and consist only of the supported client-plane efforts
`minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`. The service
MUST trim, case-normalize, de-duplicate, and return entries in canonical
catalog order.

`allowedReasoningEfforts` MUST be mutually exclusive with
`enforcedReasoningEffort`. Create and PATCH requests MUST validate the
effective persisted state, including an unchanged counterpart field. Existing
API keys whose persisted allowlist is null MUST remain unrestricted.
The persistence layer MUST reject a row that contains both an allowlist and a
fixed reasoning effort.
If legacy or manually edited storage contains a malformed non-null allowlist,
the service MUST remain fail-closed for explicit efforts and the dashboard MUST
NOT clear that sentinel during an unrelated edit. A concurrent update that
loses the mutual-exclusion constraint race MUST return the normal dashboard
validation error instead of an internal server error.

#### Scenario: Create an effort-selectable key

- **WHEN** an administrator creates an API key with
  `allowedReasoningEfforts: ["XHIGH", "low", "high", "low"]`
- **THEN** the response returns `allowedReasoningEfforts` as
  `["low", "high", "xhigh"]`
- **AND** `enforcedReasoningEffort` is null

#### Scenario: Reject an empty allowlist

- **WHEN** an administrator creates or updates an API key with
  `allowedReasoningEfforts: []`
- **THEN** the dashboard API returns 400
- **AND** the API key is not changed

#### Scenario: Reject conflicting reasoning policies on update

- **GIVEN** an API key has `enforcedReasoningEffort: "low"`
- **WHEN** an administrator updates only `allowedReasoningEfforts` to
  `["low", "medium"]`
- **THEN** the dashboard API returns 400
- **AND** the existing fixed effort remains unchanged

#### Scenario: Existing key remains unrestricted

- **GIVEN** an API key created before `allowedReasoningEfforts` existed
- **WHEN** it is read or used without that field configured
- **THEN** its response contains `allowedReasoningEfforts: null`
- **AND** no reasoning-effort allowlist is applied

#### Scenario: Unrelated edit preserves a malformed fail-closed policy

- **GIVEN** an API key exposes an empty allowlist sentinel for malformed stored
  policy data
- **WHEN** an administrator changes only its name
- **THEN** the dashboard update omits `allowedReasoningEfforts`
- **AND** the malformed persisted policy is not replaced with null

#### Scenario: Concurrent policy conflict returns a validation error

- **GIVEN** concurrent updates try to set a fixed effort and an allowlist on
  the same unrestricted key
- **WHEN** the database mutual-exclusion constraint rejects the losing update
- **THEN** the dashboard API returns its normal invalid API-key payload error

### Requirement: Dashboard manages selectable reasoning efforts

The API-key create and edit dialogs SHALL present the supported reasoning
efforts as an accessible multi-select when no fixed effort is selected. The UI
MUST represent no selected values as `null`, not an empty allowlist. When an
administrator selects a fixed effort, the UI MUST clear and disable the
allowlist; when it selects one or more allowlist values, it MUST clear the
fixed-effort selection.

#### Scenario: Configure all normal efforts without max or ultra

- **WHEN** an administrator selects `minimal`, `low`, `medium`, `high`, and
  `xhigh` in the API-key dialog
- **THEN** the saved key returns exactly those five allowed efforts
- **AND** the dialog does not show `max` or `ultra` as selected

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
The persistence layer MUST prevent a serving version that does not understand
the allowlist from creating a new API key without an explicit policy schema
version. It MUST also reject a row that contains both an allowlist and a fixed
reasoning effort, so an older partial update cannot create an ambiguous policy.

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

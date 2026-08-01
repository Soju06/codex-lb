# responses-api-compat Delta

## ADDED Requirements

### Requirement: Responses-Lite exact manifest proof tolerates verified historical developer interleaving

When a fresh durable HTTP bridge classifies a client-unanchored Responses-Lite
full resend whose `additional_tools` bundle preserves developer messages inline,
the exact durable pending-tool proof MUST allow a valid `developer` message
between a supported direct tool call and that call's matching output in the
fingerprint-verified stored prefix.

The developer message MUST have no response-owned ID or phase, MUST have no
status or a `completed` status, MUST pass the existing account-neutral metadata,
field, and content validation, and MUST NOT settle or reorder the pending call.
Classification MUST retain response-owned developer-message ID evidence until
this check has completed, even when other response-owned IDs are projected out.
The matching output MUST remain present with the same call ID and type. This
exception MUST NOT apply to the alternative retained assistant-output proof or
to the fresh suffix. The fresh suffix MUST remain a complete direct call/output
set exactly equal to the durable manifest.

This exception MUST apply only when the developer message remains inline in the
validated Responses-Lite input. Non-Lite `input` or `messages` forms whose
instruction-role messages are normalized into top-level `instructions` are
outside this requirement.

#### Scenario: Verified historical Responses-Lite developer message is transparent

- **GIVEN** a Responses-Lite input contains an `additional_tools` bundle
- **AND** its fingerprint-verified stored prefix contains a supported direct call
- **AND** a valid developer message appears before that call's matching output
- **AND** the fresh suffix exactly settles the durable pending-tool manifest
- **WHEN** the HTTP bridge opens a replacement session on the durable owner
- **THEN** it sends the original full input without injecting `previous_response_id`
- **AND** it sends the request once

#### Scenario: Other historical messages remain fail-closed

- **GIVEN** a supported direct call is pending in the verified stored prefix
- **WHEN** a user, assistant, system, malformed developer, or response-owned message appears before its output
- **THEN** exact manifest proof fails

#### Scenario: Historical output remains mandatory

- **GIVEN** a valid developer message follows a supported historical call
- **WHEN** the matching output is missing or has another call ID or type
- **THEN** exact manifest proof fails

#### Scenario: Fresh inline developer message is not a tool-loop item

- **GIVEN** a Responses-Lite input whose developer messages remain inline
- **AND** a durable pending-tool manifest
- **WHEN** the fresh suffix contains a developer message among its call/output items
- **THEN** exact manifest proof fails

#### Scenario: Alternative retained-output proof stays narrow

- **GIVEN** a stored prefix contains a developer-interleaved historical call
- **WHEN** the fresh suffix uses retained assistant output plus new user input instead of the exact durable manifest
- **THEN** the developer exception does not make that alternative proof pass

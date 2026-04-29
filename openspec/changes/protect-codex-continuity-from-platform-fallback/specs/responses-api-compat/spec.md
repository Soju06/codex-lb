## ADDED Requirements

### Requirement: Backend Codex Platform fallback preserves selectable ChatGPT continuity owners
For HTTP `/backend-api/codex/responses` and `/backend-api/codex/responses/compact` requests, downstream Codex session headers SHALL remain non-blocking transport hints for OpenAI Platform capability checks. However, when such a request resolves to a ChatGPT Web `codex_session` sticky target, the service MUST suppress usage-drain Platform fallback while that sticky ChatGPT target remains selectable. For `/backend-api/codex/responses` requests with `x-codex-turn-state`, the service MUST NOT move the request to another ChatGPT Web account solely because the sticky target exceeds the sticky budget threshold. If the sticky target is not selectable, no sticky target exists, or force fallback is enabled, the service MAY use the existing Platform fallback path.

#### Scenario: Sticky backend Codex turn-state session stays on selectable ChatGPT owner
- **WHEN** a backend Codex HTTP request includes `x-codex-turn-state` or an explicit session header
- **AND** that header maps to a ChatGPT Web `codex_session` sticky target
- **AND** the sticky target remains selectable despite being above usage-drain thresholds
- **THEN** the service routes the request to ChatGPT Web instead of OpenAI Platform
- **AND** when the request includes `x-codex-turn-state`, the service preserves the sticky ChatGPT Web owner during ChatGPT account selection

#### Scenario: Owner-forwarded backend Codex turn-state session keeps budget guard
- **WHEN** a backend Codex HTTP request with `x-codex-turn-state` is forwarded to the HTTP bridge owner instance
- **AND** the forwarded request is handled by `/internal/bridge/responses`
- **THEN** the owner instance preserves the same sticky budget reallocation guard used by the public backend Codex `/responses` route
- **AND** the owner instance does not move the request to another ChatGPT Web account solely because the sticky target exceeds the sticky budget threshold

#### Scenario: Unowned backend Codex session header can still fall back
- **WHEN** a backend Codex HTTP request includes `x-codex-turn-state` or an explicit session header
- **AND** no ChatGPT Web `codex_session` sticky target exists for that header
- **AND** ChatGPT Web usage-drain fallback conditions are met
- **THEN** the service may route the request to OpenAI Platform

#### Scenario: Unavailable sticky owner can still fall back
- **WHEN** a backend Codex HTTP request includes `x-codex-turn-state` or an explicit session header
- **AND** that header maps to a ChatGPT Web `codex_session` sticky target
- **AND** the sticky target is not selectable
- **AND** ChatGPT Web usage-drain fallback conditions are met
- **THEN** the service may route the request to OpenAI Platform

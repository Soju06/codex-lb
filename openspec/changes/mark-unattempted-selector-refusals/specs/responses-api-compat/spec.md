## ADDED Requirements

### Requirement: Proven unattempted selector refusal metadata
Raw HTTP Responses refusals MAY include `X-Codex-LB-No-Upstream-Attempt: 1` only when the local selector refuses the request before any upstream attempt has begun for that whole request. Selecting any account MUST permanently retire proof eligibility before freshness work can contact upstream, including an OAuth token exchange. A prior attempt MUST permanently prevent this proof from being attached to later selector failures. The proxy MUST preserve existing status codes and JSON/SSE error bodies. It MUST NOT infer proof from an error code, upstream payload/header or client-supplied metadata. Bridge/forwarded paths without whole-request proof MUST remain unmarked. Header absence means unknown and MUST NOT imply either dispatch or safety to replay; the header itself MUST NOT change replay policy.

#### Scenario: Initial selector refuses a raw request
- **WHEN** the selector refuses before any upstream attempt on a raw HTTP Responses request
- **THEN** the response carries the proof header without changing its existing status or JSON/SSE body

#### Scenario: An earlier upstream attempt precedes refusal
- **WHEN** any upstream attempt has begun and the selector later cannot choose another account
- **THEN** the response MUST NOT carry the proof header

#### Scenario: Upstream or client spoofs selector-looking metadata
- **WHEN** upstream or client metadata contains a selector-looking error or proof value
- **THEN** the proxy MUST NOT treat it as trusted local proof

#### Scenario: Upstream effects are uncertain
- **WHEN** an upstream quota response, timeout, disconnect or unknown-effect failure occurs
- **THEN** the response MUST NOT carry the proof header

#### Scenario: Refresh precedes a selector refusal
- **WHEN** an account is selected and freshness work may exchange tokens before a later selector refusal
- **THEN** the response MUST NOT carry the proof header, even if no Responses generation began

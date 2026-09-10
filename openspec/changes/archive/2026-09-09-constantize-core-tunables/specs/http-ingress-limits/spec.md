## MODIFIED Requirements

### Requirement: HTTP ingress reuses existing budgets

The service MUST use the fixed general HTTP body budget (`MAX_DECOMPRESSED_BODY_BYTES`, 32 MiB, in `app/core/ingress_limits.py`) as the general raw and decompressed HTTP request-body budget. When an owning route capability defines a larger fixed budget (the Responses budget `MAX_DECOMPRESSED_RESPONSES_BODY_BYTES`, 128 MiB, in the same module), the ingress guard MUST use that route budget. Neither budget is operator-configurable; the HTTP ingress guard MUST NOT add a setting or change the fixed values.

Route-specific budget and error-envelope selection MUST use the application-relative route path after removing any matching ASGI `root_path` prefix.

The generic guard MUST apply to requests solely because they declare `multipart/form-data`; the client-declared media type MUST NOT grant an exemption. An owning route capability MAY define an exact method/path-scoped authorization-before-read contract and dedicated bounded multipart parser. Only unencoded multipart requests to that exact operation, or requests marked by its outer content-encoding gate, MAY bypass generic admission. The gate MUST identify its operation independently of the declared media type, remove the encoding and mark the scope as handled without consuming the body, and the exception MUST NOT apply to any other operation.

#### Scenario: Another HTTP path uses the general budget

- **WHEN** a guarded request targets any other HTTP path
- **THEN** its raw and decompressed HTTP ingress budget is the fixed 32 MiB general budget

#### Scenario: Route-owned unencoded multipart uses dedicated admission

- **GIVEN** an exact operation has a capability-defined authorization-before-read contract and dedicated bounded multipart parser
- **WHEN** an unencoded request to that operation declares media type `multipart/form-data`
- **THEN** the generic raw whole-body guard does not preempt operation authorization or its dedicated parser limit

#### Scenario: Unrelated unencoded multipart remains guarded

- **WHEN** an unencoded request outside a route-owned multipart operation declares media type `multipart/form-data`
- **THEN** the service applies the generic raw-body budget
- **AND** the declared media type alone does not bypass admission

#### Scenario: Encoded multipart remains guarded

- **WHEN** a `multipart/form-data` request outside a route-owned multipart exception carries a `Content-Encoding` header
- **THEN** the service applies both the raw and decompressed budget checks

#### Scenario: Route-owned multipart admission can preserve authorization precedence

- **GIVEN** an exact operation has a capability-defined outer content-encoding gate, authorization-before-read contract, and dedicated bounded multipart parser
- **WHEN** an encoded request targets that operation, regardless of its declared media type
- **THEN** the generic raw and decompressed-body guards do not preempt operation authorization or its dedicated parser limit
- **AND** encoded multipart requests to all other operations remain guarded

#### Scenario: Mounted Responses route keeps its route-specific policy

- **GIVEN** the service is mounted under a non-empty ASGI `root_path`
- **WHEN** the request scope path includes that prefix and targets `/v1/responses` relative to the application
- **THEN** the service applies the Responses-specific ingress budget
- **AND** any ingress failure uses the OpenAI-compatible error envelope

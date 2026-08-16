## ADDED Requirements

### Requirement: Thread-goal OpenAPI operations have unique stable identifiers
The generated OpenAPI document MUST assign a unique `operationId` to every documented HTTP operation. The GET and POST operations at `/backend-api/codex/thread/goal/get` MUST remain available through the same runtime behavior and MUST expose the deterministic identifiers `thread_goal_get_backend_api_codex_thread_goal_get_get` and `thread_goal_get_backend_api_codex_thread_goal_get_post`, respectively. Correcting this schema metadata MUST NOT change either method's authentication, dependency, request forwarding, upstream operation, response status, or response payload behavior.

#### Scenario: Full OpenAPI schema has unique operation identifiers
- **WHEN** an unauthenticated client requests `GET /openapi.json`
- **THEN** every documented HTTP operation has an `operationId`
- **AND** no two documented HTTP operations share an `operationId`

#### Scenario: Thread-goal methods publish deterministic identifiers
- **WHEN** an unauthenticated client inspects `/openapi.json`
- **THEN** `GET /backend-api/codex/thread/goal/get` has `operationId` `thread_goal_get_backend_api_codex_thread_goal_get_get`
- **AND** `POST /backend-api/codex/thread/goal/get` has `operationId` `thread_goal_get_backend_api_codex_thread_goal_get_post`

#### Scenario: Thread-goal runtime forwarding remains compatible
- **WHEN** a client invokes either GET or POST `/backend-api/codex/thread/goal/get` with valid existing dependencies
- **THEN** the request is forwarded through the existing thread-goal handler using the original request method
- **AND** the upstream operation, response status, and response payload remain unchanged

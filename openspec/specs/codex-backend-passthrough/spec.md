# codex-backend-passthrough Specification

## Purpose
Lets a Codex client whose `chatgpt_base_url` points at codex-lb's
`/backend-api` show pooled quota while its other ChatGPT-backend features
(account checks, user settings, plugins, cloud tasks) keep working. codex-lb
serves pooled usage itself and forwards the backend calls it does not serve to
upstream ChatGPT under the caller's own identity.

## Requirements

### Requirement: Pooled usage is served on the backend-api path

codex-lb MUST serve `GET /backend-api/wham/usage` (with or without a trailing
slash) with the same authentication and the same pooled payload as
`GET /api/codex/usage`. codex-lb MUST serve
`POST /backend-api/wham/rate-limit-reset-credits/consume` (with or without a
trailing slash) with the same authentication and behavior as
`POST /api/codex/rate-limit-reset-credits/consume`.

#### Scenario: Usage is pooled on the wham path

- **GIVEN** two active pool accounts at 96% and 9% of their primary window with equal plan capacity
- **WHEN** a caller with a valid ChatGPT identity for one of them sends `GET /backend-api/wham/usage`
- **THEN** codex-lb responds `200` with a primary-window `used_percent` of about 52
- **AND** the body is identical to what `GET /api/codex/usage` returns for the same caller

### Requirement: Unserved backend paths are forwarded upstream unchanged

codex-lb MUST forward any `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, or `DELETE`
request to `/backend-api/<rest>` that no other codex-lb route serves to
`<upstream base>/<rest>`, where `<upstream base>` is the configured upstream
base URL ending in `/backend-api`, preserving `<rest>` and the raw query string
byte-for-byte (no decoding or re-encoding), so a percent-encoded `<rest>` such
as `settings%2Fdetail` reaches upstream still encoded. A path whose decoded form
contains a `.` or `..` segment, `?`, `#`, `\`, or a control character MUST NOT be
forwarded. Paths codex-lb serves itself MUST
keep their existing behavior under every method, including their `405` answer
to a method they do not serve.
Unserved paths under `/backend-api/codex/`, `/backend-api/files`, and
`/backend-api/transcribe` MUST NOT be forwarded, because those namespaces carry
pool-routed traffic. A request that is not forwarded (because of its
namespace, a path that would leave upstream `/backend-api/`, or its
credentials, as specified below) MUST receive exactly the response codex-lb
gives a path no route serves, including status code and error envelope, and
MUST cause no upstream request. codex-lb MUST NOT forward paths outside
`/backend-api/`.

#### Scenario: Account check is forwarded

- **GIVEN** the caller presents a valid ChatGPT identity for an active pool account
- **WHEN** it sends `GET /backend-api/wham/accounts/check`
- **THEN** codex-lb sends `GET <upstream base>/wham/accounts/check` upstream
- **AND** relays the upstream status code and body to the caller

#### Scenario: Plugin listing is forwarded with its query string

- **GIVEN** the caller presents a valid ChatGPT identity for an active pool account
- **WHEN** it sends `GET /backend-api/ps/plugins/list?scope=GLOBAL&limit=200`
- **THEN** the upstream request targets `<upstream base>/ps/plugins/list?scope=GLOBAL&limit=200`

#### Scenario: Query string encoding is preserved

- **GIVEN** the caller presents a valid ChatGPT identity for an active pool account
- **WHEN** it sends `GET /backend-api/wham/echo?q=a+b&x=%2Fy&z=%7e`
- **THEN** the upstream request targets `<upstream base>/wham/echo?q=a+b&x=%2Fy&z=%7e`

#### Scenario: Path percent-encoding is preserved

- **GIVEN** the caller presents a valid ChatGPT identity for an active pool account
- **WHEN** it sends `GET /backend-api/settings%2Fdetail`
- **THEN** the upstream request targets `<upstream base>/settings%2Fdetail`, not `<upstream base>/settings/detail`

#### Scenario: Encoded traversal or query delimiters are not forwarded

- **GIVEN** the caller presents a valid ChatGPT identity for an active pool account
- **WHEN** it sends `GET /backend-api/%2e%2e%2fsecret` or `GET /backend-api/x%3Fy`
- **THEN** codex-lb responds with its unmatched-path `404`
- **AND** no upstream request is made

#### Scenario: Wrong method on a served path is not forwarded

- **GIVEN** the caller presents a valid ChatGPT identity for an active pool account
- **WHEN** it sends `POST /backend-api/wham/usage`
- **THEN** codex-lb responds `405`
- **AND** no request is forwarded upstream

#### Scenario: Pool-routed namespaces are not forwarded

- **GIVEN** the caller presents a valid ChatGPT identity for an active pool account
- **WHEN** it sends `GET /backend-api/codex/not-a-served-route`
- **THEN** codex-lb responds `404` with the root not-found envelope
- **AND** no upstream request is made

#### Scenario: Trailing-slash alias keeps its method-not-allowed answer

- **WHEN** a caller sends `POST /backend-api/codex/images/generations/`
- **THEN** codex-lb responds `405` with the same body as `POST /v1/images/generations/`
- **AND** no upstream request is made

#### Scenario: Served routes are not forwarded

- **WHEN** a caller sends `GET /backend-api/wham/usage`
- **THEN** codex-lb returns its pooled usage payload
- **AND** no upstream `/wham/usage` request is made on the caller's behalf beyond the existing usage-identity validation

### Requirement: Forwarding uses the caller's own ChatGPT identity

A forwarded request MUST carry the caller's own bearer token and
`chatgpt-account-id` upstream. codex-lb MUST NOT substitute, add, or fall back
to any pool account's stored credentials. codex-lb MUST forward only when the
caller's `chatgpt-account-id` matches an active account in the pool, and
only after upstream has accepted the caller's bearer token for that account on
the usage-identity check (`GET <upstream base>/wham/usage`, the check
`/api/codex/usage` uses). A confirmed token/account binding MAY be reused for
up to 60 seconds without repeating the check. When upstream rejects the token
on that check, codex-lb MUST respond `401` with an `authentication_error`
envelope and MUST NOT forward the request. A request
without an `Authorization` bearer token MUST NOT be forwarded and MUST receive
the unserved-path response. When a bearer token is present but the
`chatgpt-account-id` header is missing, or the account is unknown or inactive,
codex-lb MUST respond `401` with an `authentication_error` envelope without
contacting upstream.

#### Scenario: Caller credentials are sent unchanged

- **GIVEN** pool account A is active and the caller authenticates as account A
- **WHEN** the caller sends `GET /backend-api/wham/settings/user`
- **THEN** the upstream request carries the caller's bearer token and `chatgpt-account-id`
- **AND** does not carry account A's stored access token

#### Scenario: Unknown account is rejected locally

- **GIVEN** no active pool account has `chatgpt-account-id` X
- **WHEN** a caller sends `GET /backend-api/wham/settings/user` with `chatgpt-account-id: X`
- **THEN** codex-lb responds `401` with an `authentication_error` envelope
- **AND** no upstream request is made

#### Scenario: Token not accepted for the account is not forwarded

- **GIVEN** pool account A is active
- **AND** upstream rejects the caller's bearer token on the usage-identity check for account A
- **WHEN** the caller sends `GET /backend-api/wham/settings/user` with account A's `chatgpt-account-id`
- **THEN** codex-lb responds `401` with an `authentication_error` envelope
- **AND** no request other than the usage-identity check leaves through account A's egress

#### Scenario: Uncredentialed connector call falls through

- **WHEN** a caller sends `POST /backend-api/ps/mcp` without an `Authorization` bearer token
- **THEN** codex-lb responds exactly as it does to `POST /backend-api/codex/does-not-exist`
- **AND** no upstream request is made

#### Scenario: Anonymous unknown path keeps the root envelope

- **WHEN** a caller sends `GET /backend-api/does-not-exist` with no credentials
- **THEN** codex-lb responds `404` with `{"error": {"message": "Not Found", "type": "invalid_request_error", "code": "not_found"}}`

#### Scenario: Upstream token rejection is relayed

- **GIVEN** the caller's `chatgpt-account-id` matches an active pool account
- **AND** upstream accepts the caller's token on the usage-identity check but rejects it on the forwarded call with `401`
- **WHEN** the caller sends `GET /backend-api/wham/accounts/check`
- **THEN** codex-lb relays the upstream `401` status and body
- **AND** does not change any pool account's status or health

### Requirement: Proxy API-key principals are not forwarded

A request that authenticates with a codex-lb API key (an `sk-clb-` bearer,
with or without `chatgpt-account-id`), or that carries
`X-Codex-LB-Required-Capability`,
MUST NOT be forwarded upstream. It MUST receive the unserved-path response
and cause no upstream request.

#### Scenario: API-key caller gets not-found

- **WHEN** a caller sends `GET /backend-api/wham/settings/user` with `Authorization: Bearer sk-clb-…` and no `chatgpt-account-id`
- **THEN** codex-lb responds `404`
- **AND** no upstream request is made

#### Scenario: Account header does not make an API key forwardable

- **GIVEN** pool account A is active
- **WHEN** a caller sends `GET /backend-api/wham/settings/user` with `Authorization: Bearer sk-clb-…` and account A's `chatgpt-account-id`
- **THEN** codex-lb responds `404`
- **AND** no upstream request is made

### Requirement: Forwarded requests use the account's upstream egress route

A forwarded request MUST leave through the upstream egress route resolved for
the matched pool account, the same route its usage-identity validation uses.
When that route cannot be resolved, codex-lb MUST respond `503` with an
`upstream_error` envelope, matching the usage-identity path, and MUST NOT fall
back to direct egress.

#### Scenario: Account bound to an upstream proxy

- **GIVEN** the matched pool account is bound to an upstream proxy pool
- **WHEN** a forwarded request is sent
- **THEN** the upstream request goes out through that account's resolved proxy route

#### Scenario: Unresolvable route fails closed

- **GIVEN** the matched pool account's upstream route cannot be resolved
- **WHEN** the caller sends `GET /backend-api/wham/accounts/check`
- **THEN** codex-lb responds `503` with an `upstream_error` envelope
- **AND** no direct-egress upstream request is made

### Requirement: Forwarded bodies and headers are relayed faithfully

codex-lb MUST relay the request body unchanged, for every forwarded method
including `GET` and `HEAD`, and MUST stream the upstream
response body to the caller as it arrives, without waiting for the upstream
response to complete. It MUST relay the upstream status code and only an
allowlist of end-to-end response headers, which MUST include `Content-Type`,
`Location`, `Mcp-Session-Id`, `Retry-After`, and `WWW-Authenticate`. codex-lb
MUST NOT follow upstream redirects: a `3xx` response MUST be relayed to the
caller with its status and `Location`. It MUST NOT forward
`Cookie` upstream, and MUST NOT relay `Set-Cookie`, hop-by-hop headers, or any
other header outside the allowlist back to the caller.

#### Scenario: Event stream is streamed

- **GIVEN** upstream answers a forwarded request with `Content-Type: text/event-stream` and emits events over time
- **WHEN** the caller sends that request through codex-lb
- **THEN** the caller receives each event as upstream emits it
- **AND** the response carries `Content-Type: text/event-stream`

#### Scenario: Session header round-trips

- **GIVEN** upstream returns `Mcp-Session-Id: s1`
- **WHEN** the caller later sends a forwarded request with `Mcp-Session-Id: s1`
- **THEN** the caller saw `Mcp-Session-Id: s1` on the first response
- **AND** the later upstream request carries `Mcp-Session-Id: s1`

#### Scenario: Redirects are relayed, not followed

- **GIVEN** upstream answers a forwarded request with `302` and `Location: /backend-api/wham/elsewhere`
- **WHEN** the caller sends that request through codex-lb
- **THEN** the caller receives `302` with that `Location`
- **AND** codex-lb makes no request to `/backend-api/wham/elsewhere`

#### Scenario: GET body is relayed

- **WHEN** the caller sends a forwarded `GET` with a request body
- **THEN** the upstream request carries the same body

#### Scenario: Cookies do not cross the proxy

- **GIVEN** upstream responds with a `Set-Cookie` header
- **WHEN** a caller that sent a `Cookie` header makes a forwarded request
- **THEN** the upstream request carries no `Cookie` header
- **AND** the response to the caller carries no `Set-Cookie` header

### Requirement: Forwarded calls and declines are logged for operators

Each forwarded call MUST produce one log record when its response ends. The
record MUST include the method, the forwarded path, the matched pool account
id, the upstream status, an outcome of `completed`, `client_disconnect`, or
`error` (with the error type), the duration in milliseconds, and the number of
body bytes relayed. It MUST be logged at WARNING when the outcome is `error` or
the upstream status is 5xx, and at INFO otherwise. A request the passthrough
declines to forward MUST produce a DEBUG record with its reason
(`unsafe_path`, `closed_namespace`, `capability_header`, `no_bearer`,
`api_key_principal`, or `served_locally`). The identity check MUST produce a DEBUG record naming the
matched pool account and its egress (`direct` or the proxy pool). No record
MUST contain the caller's token.

#### Scenario: Completed call

- **WHEN** a forwarded call's body is fully relayed
- **THEN** an INFO record carries `outcome=completed`, the status, the duration, and the relayed byte count

#### Scenario: Upstream fails mid-stream

- **GIVEN** upstream drops the connection after sending part of the body
- **WHEN** the call ends
- **THEN** a WARNING record carries `outcome=error` and the error type

#### Scenario: Upstream socket error is not mistaken for a caller disconnect

- **GIVEN** the upstream body iterator raises an aiohttp socket-level error (e.g. `ClientOSError`, an `OSError` subclass)
- **WHEN** the call ends
- **THEN** a WARNING record carries `outcome=error` and the error type, under both ASGI 2.3 and 2.4 servers

#### Scenario: Caller disconnects

- **WHEN** the caller disconnects before the body finishes
- **THEN** an INFO record carries `outcome=client_disconnect`

#### Scenario: Declined request

- **WHEN** a request without a bearer token reaches an unserved `/backend-api/` path
- **THEN** a DEBUG record carries `reason=no_bearer`

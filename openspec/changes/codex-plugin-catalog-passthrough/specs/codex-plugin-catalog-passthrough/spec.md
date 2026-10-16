## ADDED Requirements

### Requirement: Codex plugin-catalog reads are forwarded upstream verbatim

codex-lb MUST serve `GET /plugins/featured` and `GET /ps/plugins/{path}` at its origin root and forward each request upstream as a Codex control request to `{upstream_base_url}/plugins/featured` and `{upstream_base_url}/ps/plugins/{path}` respectively — the paths MUST NOT receive the `codex/` namespace prefix that other control requests get. The forwarded request MUST preserve the method and the full query string (every key, repeated keys included, in arrival order) and MUST carry no request body. The response MUST return the upstream status code and body verbatim together with the control-response header allowlist. The request MUST be served with pool account credentials selected like other Codex control requests, with session affinity applied, and MUST NOT replay the caller's bearer token upstream. Methods other than GET on these paths MUST be rejected with HTTP 405 without contacting upstream.

#### Scenario: Catalog listing is forwarded with its query string
- **GIVEN** at least one active account in the pool
- **WHEN** a client calls `GET /ps/plugins/list?scope=GLOBAL&limit=200`
- **THEN** codex-lb issues `GET {upstream_base_url}/ps/plugins/list?scope=GLOBAL&limit=200` with a pool account's credentials
- **AND** returns the upstream status, body, and allowlisted headers unchanged

#### Scenario: Per-plugin detail read before an install is forwarded
- **WHEN** a client calls `GET /ps/plugins/gmail?includeDownloadUrls=true`
- **THEN** codex-lb issues `GET {upstream_base_url}/ps/plugins/gmail?includeDownloadUrls=true` and returns the upstream response unchanged

#### Scenario: Featured plugins are forwarded from the root namespace
- **WHEN** a client calls `GET /plugins/featured?platform=codex`
- **THEN** codex-lb issues `GET {upstream_base_url}/plugins/featured?platform=codex`, not `{upstream_base_url}/codex/plugins/featured`

#### Scenario: Writes to the catalog namespace are refused
- **WHEN** a client sends `POST /ps/plugins/list`
- **THEN** codex-lb returns HTTP 405
- **AND** no upstream request is made

### Requirement: Unknown plugin-catalog paths return the API not-found envelope

A request to a path under `/ps/` or `/plugins/` that codex-lb does not serve MUST return HTTP 404 with the OpenAI-style error envelope (`code = "not_found"`), and MUST NOT be answered by the dashboard SPA fallback.

#### Scenario: Unknown catalog path fails fast as JSON
- **WHEN** a client calls `GET /ps/does-not-exist`
- **THEN** the response is HTTP 404 with `error.code = "not_found"`
- **AND** the body is not the dashboard `index.html`

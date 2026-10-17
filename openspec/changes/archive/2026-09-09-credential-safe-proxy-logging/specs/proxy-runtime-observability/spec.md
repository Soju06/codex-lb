## ADDED Requirements

### Requirement: Rendered log records redact URL userinfo and keyed secrets

Every log record rendered by the application's text, access, and JSON
formatters MUST have URL userinfo, in both the `scheme://user:password@` and
the username-only `scheme://user@` forms, replaced with
`scheme://[REDACTED]@` and `Basic <token>` authorization tokens (in the
`Basic`, `basic` and `BASIC` scheme spellings)
(a reversible encoding of `user:password`, as carried in aiohttp proxy-error
reprs) replaced with `Basic [REDACTED]`, regardless of the originating logger
(application, `asyncio`, aiohttp, uvicorn) and including exception and stack
text. Structured extra keys MUST be redacted like values. Records at WARNING
level or higher MUST additionally have keyed secrets (`password=`, `token=`,
`api_key=`, bearer, basic and authorization values in any letter case, JSON
secret fields embedded in strings, and structured extra fields whose key names
a secret, whatever the value type) redacted. Redaction MUST never raise and
MUST fail closed: if a redaction pass fails, the affected text is replaced with
a `[REDACTED: log redaction failed]` placeholder and the record is still
emitted with its timestamp, level and logger; structured extras that are cyclic,
pathologically deep, or unprintable MUST still be emitted with redaction
applied to every finite, printable part. Application startup MUST route
`warnings.warn` output through the same log handlers. Log records that contain
no secret patterns MUST render byte-identically to the unredacted rendering.

#### Scenario: Unclosed aiohttp connection repr is credential-free

- **GIVEN** an aiohttp connection is finalized without release and its connection key holds a credentialed proxy URL
- **WHEN** the loop exception handler logs `Unclosed connection` through the `asyncio` logger
- **THEN** the rendered record contains `proxy=URL('scheme://[REDACTED]@host:port')`
- **AND** the password appears in neither the text nor the JSON rendering

#### Scenario: Userinfo containing unencoded sub-delims is redacted

- **GIVEN** a proxy password containing an RFC 3986 sub-delim such as `'`, which yarl leaves unencoded in the URL userinfo, or a raw environment proxy string that is not percent-encoded at all
- **WHEN** the URL is rendered in any log record at any level
- **THEN** the record contains `scheme://[REDACTED]@host:port` and neither the raw nor the percent-encoded password

#### Scenario: Username-only URL userinfo is redacted

- **GIVEN** a URL whose userinfo carries only a username (a token used as the username, `scheme://user@host:port`) with no `:password` part
- **WHEN** the URL is rendered in any log record at any level, text or JSON
- **THEN** the record contains `scheme://[REDACTED]@host:port` and the username does not appear

#### Scenario: Proxy error repr with a Basic token is masked

- **GIVEN** an aiohttp proxy error whose tunnel request headers carry `Proxy-Authorization: Basic <token>`
- **WHEN** the error is logged with `%r` at any level, or its repr is logged by the loop's exception handler for an unretrieved task
- **THEN** the rendered record contains `'Proxy-Authorization': 'Basic [REDACTED]'`
- **AND** neither the token nor the password appears in the text or JSON rendering

#### Scenario: Secret-keyed structured extras are masked

- **GIVEN** a WARNING or higher record carries an extra field such as `{"password": "..."}` or `{"access_token": "..."}`
- **WHEN** the JSON formatter renders the record
- **THEN** the field value is replaced with `[REDACTED]` whatever its type (string, list, number, bytes, mapping); a null value stays null
- **AND** fields such as `attempt` or `tokens` keep their values
- **AND** an extra key carrying URL userinfo is rendered as `scheme://[REDACTED]@host`

#### Scenario: Secret-free records are unchanged

- **WHEN** a record such as the one-time bootstrap token banner contains no URL userinfo or keyed secret pattern
- **THEN** the rendered output is byte-identical to the unredacted rendering

#### Scenario: Redaction failure never breaks logging and fails closed

- **WHEN** a redaction pass raises while rendering a record
- **THEN** the record is still emitted, in text and JSON, with its timestamp, level and logger
- **AND** the affected text is rendered as `[REDACTED: log redaction failed]` rather than the original text

#### Scenario: Cyclic or unprintable structured extras never drop the record

- **GIVEN** a record carries an extra whose container refers back to itself, or whose `repr()` raises
- **WHEN** the JSON formatter renders the record
- **THEN** the record is emitted, the back-reference collapses to a `{...}` / `[...]` placeholder and the unprintable value to an `<unprintable ...>` marker
- **AND** secret-keyed fields and URL userinfo in the finite part of the extra are still redacted

#### Scenario: Secret-keyed mappings rendered as Python repr are masked

- **GIVEN** a WARNING or higher record renders a mapping with `%r`, or the JSON formatter falls back to text for a structured extra (nesting beyond the depth limit, a container whose iteration raises, an unserializable rebuild)
- **WHEN** the rendered text contains `'password': 'x'`, `'access_token': [...]` or `'api_key': 123`
- **THEN** each secret-keyed value is replaced with `[REDACTED]` (quotes kept for quoted strings)
- **AND** `'Proxy-Authorization': 'Basic <token>'` keeps rendering as `'Basic [REDACTED]'`

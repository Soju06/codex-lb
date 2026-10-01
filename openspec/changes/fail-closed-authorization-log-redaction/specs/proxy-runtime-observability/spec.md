## MODIFIED Requirements

### Requirement: Secret-pattern redaction stays on the current line

Keyed, bearer, basic, authorization, JSON, and Python-repr secret patterns
MUST be applied independently to each CR/LF-delimited line of rendered log
text. A match MUST NOT consume CR or LF or any text from a following line.
Unterminated JSON secret values MUST be redacted through the end of the
current line. A Bearer credential MUST treat a glued `:` tail on the same
line as credential material. Same-line comma and ampersand separators MUST
keep their existing truncation behavior for Basic credentials and for keyed
and Bearer values. An authorization value whose scheme is not Bearer or Basic,
including one whose key is followed by a closing double quote before the
separator, MUST be redacted through the end of the current line. Records below WARNING MUST still
skip these keyed patterns.

#### Scenario: Authorization does not swallow the next traceback line

- **GIVEN** a WARNING or higher record whose exception text contains
  `authorization=Basic X` followed by a newline and `status=failed`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the Basic credential is replaced with `[REDACTED]`
- **AND** the following line still contains `status=failed`

#### Scenario: Unterminated JSON secret is redacted through end of line

- **GIVEN** a WARNING or higher record contains `{"token":"abc` with no
  closing quote before the line ending, then a following `safe diagnostic line`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the token value is replaced with `[REDACTED]`
- **AND** `safe diagnostic line` remains

#### Scenario: Bearer glued colon tail is redacted

- **GIVEN** a WARNING or higher record contains
  `Bearer abc.def:GLUEDTAIL, status=502`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the rendered text contains `Bearer [REDACTED], status=502`
- **AND** neither `abc.def` nor `GLUEDTAIL` appears

#### Scenario: Same-line authorization truncation is unchanged

- **GIVEN** a WARNING or higher record contains
  `Authorization: Basic dXNlcjpwYXNz status=failed` on one line with no comma
- **WHEN** the text formatter renders the record
- **THEN** the credential is redacted
- **AND** `status=failed` is not present on that line

#### Scenario: Line terminators are preserved and redaction is idempotent

- **GIVEN** rendered secret-bearing text that uses LF and CRLF separators
- **WHEN** secret-pattern redaction is applied once and then again
- **THEN** the terminator bytes and line count are unchanged
- **AND** the second pass equals the first

#### Scenario: Digest auth-param list is redacted through end of line

- **GIVEN** a WARNING or higher record contains
  `Authorization: Digest username="svc", realm="api", nonce="n1", response="6629fae4"`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the rendered text contains `Authorization: [REDACTED]`
- **AND** neither `realm=` nor `response=` nor `6629fae4` appears

#### Scenario: AWS SigV4 auth-param list is redacted through end of line

- **GIVEN** a WARNING or higher record contains
  `Authorization: AWS4-HMAC-SHA256 Credential=AKID/aws4_request, SignedHeaders=host;x-amz-date, Signature=fe5f80f7`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the rendered text contains `Authorization: [REDACTED]`
- **AND** neither `Signature=` nor `fe5f80f7` appears

#### Scenario: Quoted authorization key with a Digest value is redacted

- **GIVEN** a WARNING or higher record contains
  `{"authorization": Digest username="public", malformed, response="QA_SECRET"}`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the rendered text contains `{"authorization": [REDACTED]`
- **AND** `QA_SECRET` does not appear

#### Scenario: Basic credential keeps the comma bound

- **GIVEN** a WARNING or higher record contains
  `Authorization: Basic dXNlcjpwYXNz, status=failed`
- **WHEN** the text formatter renders the record
- **THEN** the rendered text contains `Authorization: [REDACTED], status=failed`

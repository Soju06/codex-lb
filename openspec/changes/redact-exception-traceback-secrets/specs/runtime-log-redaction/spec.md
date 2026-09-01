## ADDED Requirements

### Requirement: Runtime exception tracebacks redact recognized secrets

The runtime logging system MUST apply the existing sensitive-log-value patterns
to exception traceback text emitted by the shipped text and JSON application
formatters.

#### Scenario: Recognized secrets appear in an exception message

- **WHEN** an exception message contains a recognized keyed secret, Bearer
  token, or JSON-style secret field
- **THEN** the text formatter MUST replace each secret value with `[REDACTED]`
- **AND** the JSON formatter MUST replace the same secret values with `[REDACTED]`
- **AND** neither serialized log entry may contain the original secret values

#### Scenario: A credential contains delimiters before a later field

- **WHEN** an exception message contains `authorization=Basic user:<secret>, status=ok`,
  `authorization=Bearer user:<secret>, status=ok`, or
  `authorization=Digest username=public; response=<secret>, status=ok`
- **THEN** both shipped formatters MUST omit the secret
- **AND** `status=ok` MUST remain present

#### Scenario: A JSON-style secret value is cut off by the end of its line

- **WHEN** a recognized JSON-style secret field has no closing quote before the
  end of its traceback line
- **THEN** both shipped formatters MUST redact the value through that line end,
  including a trailing backslash
- **AND** the following traceback lines MUST remain unchanged

### Requirement: Traceback redaction is scoped to single lines

The runtime logging system MUST redact traceback text one line at a time so
that no sensitive-value pattern consumes a line terminator.

#### Scenario: Redacted traceback keeps its structure

- **WHEN** a recognized secret is redacted from an exception traceback
- **THEN** the serialized traceback MUST contain the same number of lines as the
  unredacted traceback
- **AND** every line that contains no recognized secret MUST be unchanged,
  including frame locations, source lines, chained-exception separators, and
  the exception type line

#### Scenario: A sensitive value is followed by a line terminator

- **WHEN** an exception message places a recognized secret before any line
  terminator honored by `str.splitlines()`, including `\r\n`, `\u2028`, and
  `\u2029`, followed by non-sensitive text
- **THEN** both shipped formatters MUST omit the secret
- **AND** they MUST retain the line terminator and the following non-sensitive
  text

### Requirement: Cached traceback text is redacted

The text application formatter MUST NOT append an unredacted traceback that
another formatter cached on a shared `LogRecord`.

#### Scenario: Another formatter already cached the traceback

- **WHEN** a record with `exc_info` already carries unredacted `exc_text` from
  another formatter
- **THEN** the text formatter MUST emit a redacted traceback
- **AND** it MUST NOT write to the shared record, so the cached `exc_text`
  stays unchanged for other handlers

#### Scenario: A record carries only cached traceback text

- **WHEN** a record has `exc_text` but no `exc_info`
- **THEN** the text formatter MUST emit that text with recognized secrets
  redacted

### Requirement: Ordinary log-value redaction is preserved

The runtime logging system MUST preserve the existing one-line normalization
for ordinary structured error fields and MUST continue to redact every value
the existing patterns redacted. The only pattern changes are that a recognized
JSON-style secret value with no closing quote is redacted through the end of
the field, and that a Bearer credential extends to the next whitespace or
structural delimiter (`,`, `&`, `;`, quotes, backslash, closing brackets)
rather than to the first character outside the token68 alphabet.

#### Scenario: Ordinary structured field contains multiline whitespace

- **WHEN** an ordinary error field is passed through the existing log-value
  redaction entry point
- **THEN** the field MUST remain normalized to one line
- **AND** recognized secret values MUST remain redacted

#### Scenario: Ordinary structured field contains an unterminated JSON-style secret

- **WHEN** an ordinary error field contains a recognized JSON-style secret
  field whose value has no closing quote
- **THEN** the value MUST be redacted through the end of the field

#### Scenario: Ordinary structured field contains a malformed Bearer credential

- **WHEN** an ordinary error field contains `Bearer` followed by a credential
  that contains characters outside the token68 alphabet, such as `user:<secret>`
- **THEN** the whole credential up to the next whitespace or structural
  delimiter MUST be replaced with `[REDACTED]`
- **AND** fields after that delimiter MUST remain present
- **AND** a closing quote or bracket directly after a valid token MUST be kept

# Exception traceback redaction context

## Purpose and scope

The runtime logging module already recognizes keyed credentials, JSON credential fields, Bearer tokens, and non-Bearer authorization values in ordinary error fields. This change closes the remaining serializer path where `logging.Formatter.formatException()` copied an exception message into traceback output without those replacements.

The scope is the two default exception formatter families used by text and JSON application logs. Access logs and public exception responses are unchanged.

## Decision

Format the traceback first, then apply the same pattern substitutions used for ordinary log values to each traceback line independently. The ordinary one-line whitespace collapse is not applied to tracebacks.

Per-line application is what preserves diagnostic structure: no pattern can consume a line terminator, so frame locations, chained-exception separators, and the exception type line stay byte-identical unless that specific line contains a recognized secret. The set of line terminators is the one `str.splitlines()` honors, which includes `\r\n`, `\v`, `\f`, `\x1c`–`\x1e`, `\x85`, `\u2028`, and `\u2029`.

`UtcDefaultFormatter` also overrides `format()` because `logging.Formatter.format()` appends `record.exc_text` verbatim when another handler's formatter already cached a traceback on the shared record. The override always formats a shallow copy of the record, so this formatter neither emits another formatter's cached traceback nor writes its own cache or `message` onto the record other handlers see.

## Constraints

- Do not reconstruct exceptions or mutate traceback objects.
- Do not introduce a separate list of secret patterns or a credential grammar; the ordinary-field policy is the single source of truth for what counts as a secret and where a value ends.
- Keep existing one-line normalization for ordinary structured error fields. The two pattern changes apply to ordinary fields as well and only add redaction: an unterminated JSON-style value redacts through the end of its line, and a Bearer credential is bounded by whitespace or a structural delimiter (`,`, `&`, `;`, quotes, backslash, closing brackets) instead of by the token68 alphabet, so a malformed credential such as `Bearer user:<secret>` cannot leave a glued suffix while a closing quote or bracket after a valid token is kept.

## Failure modes and rejected alternatives

- Applying `_redact_log_value()` directly would collapse traceback whitespace and destroy stack structure.
- Applying the ordinary patterns to the whole traceback text lets value classes such as `[^,&]+` run across line terminators and swallow following frames.
- Matching JSON-style values across lines is unsafe: an unterminated value in an exception message would be closed by the first `"` in a later `File "..."` frame line and erase those frames. A value cut off by the end of its line is therefore redacted through that line end only.
- Parsing HTTP `Authorization` grammar to preserve same-line context after a credential while failing closed on malformed values was attempted and abandoned: the two goals are undecidable for arbitrary text, and each added rule surfaced a new boundary case.

## Known limits (pre-existing policy, unchanged here)

The ordinary-field patterns stop authorization values at `,` and `&`, only recognize `authorization` when it is directly followed by `=` or `:`, and stop Bearer and keyed values at whitespace. Inputs such as `Digest username="a,b", response="<secret>"`, `{'authorization': 'Basic <secret>'}`, or `Bearer abc <secret>` therefore keep part of the credential in ordinary error fields on `main` today and behave identically in tracebacks after this change. A whitespace-separated tail is indistinguishable from diagnostic prose such as `Bearer abc retrying upstream request`, and the quoted-key and quoted-comma forms belong to schemes and serializations this proxy does not emit. Hardening those boundaries is a separate policy change to the shared patterns.

## Example

Input traceback excerpt:

```text
Traceback (most recent call last):
  File "app/modules/proxy/service.py", line 42, in forward
    raise UpstreamError(message)
UpstreamError: authorization=Basic dXNlcjpwYXNz
status=failed
```

Serialized text and JSON traceback fields retain every line and frame:

```text
Traceback (most recent call last):
  File "app/modules/proxy/service.py", line 42, in forward
    raise UpstreamError(message)
UpstreamError: authorization=[REDACTED]
status=failed
```

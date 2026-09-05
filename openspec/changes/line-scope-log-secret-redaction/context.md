# Line-scoped log secret redaction — change context

## Purpose / scope

Harden the existing rendered-record secret patterns so they cannot destroy
traceback structure or leak glued Bearer / unterminated JSON tails. The
baseline policy is `credential-safe-proxy-logging` on
`proxy-runtime-observability`: WARNING+ keyed redaction of the fully
rendered text and JSON exception field, INFO limited to URL userinfo and
`Basic` tokens.

## Decisions

- Line-scoped application of `_redact_secret_patterns`, not a credential
  grammar. #2009's parser did not converge.
- Replacement PR for #2009 rather than rebasing the 18-round branch onto the
  mixin already on `main`.
- #2028 stays a follow-up. This change must not silently adopt fail-closed
  end-of-line authorization redaction.

## Constraints

- Do not copy `LogRecord` on the format path.
- Do not add `UtcDefaultFormatter.format` / `formatException` overrides.
- Do not lower the keyed-secret gate below WARNING.

## Failure modes

- Authorization without `,`/`&` still consumes the rest of **that** line,
  including a same-line `status=failed`. That is existing pinned behavior.
- DEBUG/INFO `exc_info=` still skips keyed patterns. Repository DEBUG sites
  that log exceptions remain out of this change.

## Example

```text
RuntimeError: authorization=Basic X
status=failed
api_key=Y
Bearer abc.def:GLUEDTAIL, ok=1
payload={"token":"abc
```

renders as

```text
RuntimeError: authorization=[REDACTED]
status=failed
api_key=[REDACTED]
Bearer [REDACTED], ok=1
payload={"token":"[REDACTED]
```

## Related

- Supersedes #2009.
- Related to #2028 (deferred classes).
- Baseline: #2053 / #2054 / `credential-safe-proxy-logging`.

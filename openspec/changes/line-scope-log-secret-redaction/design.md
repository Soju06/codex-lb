## Context

`credential-safe-proxy-logging` already redacts the fully rendered record
through `_RedactingFormatterMixin` and JSON `formatException`. The remaining
gaps are pattern bounds, not formatter architecture. PR #2009 tried a
grammar-aware RFC 9110 parser across many review rounds; that path grew
without converging. This change uses a line-scoped invariant instead.

## Goals / Non-Goals

**Goals:**

- Secret-pattern matches must not cross CR/LF.
- Unterminated JSON secrets on the current line must not leak.
- `Bearer abc.def:GLUEDTAIL` must not leave `:GLUEDTAIL`.
- Preserve existing same-line `Authorization: Basic … status=failed`
  comma-less truncation and the WARNING+ keyed-secret gate.

**Non-Goals:**

- Auth-param lists, quoted Python header keys, whitespace-separated Bearer
  tails (issue #2028).
- Changing INFO/DEBUG keyed-secret policy.
- Replacing the mixin with `format` / `formatException` overrides.
- Copying `LogRecord` on the format path.

## Decisions

Apply `_redact_secret_patterns` independently to each CR/LF-delimited line
and reattach the original terminator bytes. That is a structural invariant
(`line count and terminator bytes are unchanged`; `f(f(x)) == f(x)` for the
shipped patterns) rather than a growing credential grammar.

Make the JSON closer optional at end of line so `{"token":"abc` redacts
through `\Z` on that line.

Add `:` to the Bearer token class so a glued tail is consumed up to the
existing whitespace/comma stop.

Rejected: tightening only `[^,&]` to `[^\n,&]`. That would fix authorization
swallowing and still leave keyed `\s*` and JSON `\s*` able to cross lines.

Rejected: expanding into #2028's fail-closed-to-end-of-line authorization
policy. That drops same-line diagnostic tails and is a separate contract.

## Why

The `authorization=` redaction pattern stops at the first `,` or `&`. For
non-Bearer, non-Basic schemes that carry an auth-param list (Digest, AWS
SigV4), every member after the first comma leaks into rendered log text, for
example `response="..."` or `Signature=...`. A quoted key such as
`{"authorization": Digest ...}` is not matched at all. Upstream issue #2028
tracks this.

## What Changes

- Redact an authorization value whose scheme is not Bearer or Basic through the
  end of the current line, allowing an optional closing double quote between
  the key and the `=`/`:` separator.
- Keep the existing `,`/`&` bound for Basic credentials and for values already
  reduced by the Bearer/Basic passes.
- Leave the keyed-secret WARNING gate, the `keyed_secrets=False` path, and the
  Python-repr pattern unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Require end-of-line redaction for non-Bearer,
  non-Basic authorization values on top of the line-scoped secret-pattern
  requirement.

## Impact

- Code: `app/core/runtime_logging.py`.
- Tests: `tests/unit/test_structured_logging.py`.
- No settings, dependencies, schemas, routes, database, or frontend changes.
- Fixes #2028 for the auth-param-list and quoted-key classes. The
  whitespace-separated Bearer tail (class 3) stays out of scope.

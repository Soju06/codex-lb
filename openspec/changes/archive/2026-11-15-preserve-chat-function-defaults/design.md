## Context

The source and destination protocols assign different meanings to an omitted
function `strict` flag. The converter already distinguishes chat messages from
Responses-shaped passthrough before normalizing tools.

## Decisions

- Materialize false only inside `_normalize_chat_tools`, after the passthrough
  early return, for absent/null flags on nested and flat function definitions.
- Preserve supplied non-null values instead of using truthiness or boolean
  coercion; otherwise malformed input could become a valid false flag.
- Copy a flat tool only when adding the default. Keep schemas and original tool
  dictionaries untouched.
- Reuse the existing explicit-true schema checks and original-index error path.

## Verification

Converter regressions cover defaults, explicit booleans, malformed values,
client-object preservation, and Responses passthrough controls. HTTP tests
capture forwarded tools on streaming and non-streaming chat requests, with no
external provider calls. Existing strict-schema tests retain rejection coverage.

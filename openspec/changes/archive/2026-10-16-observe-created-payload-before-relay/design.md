## Context

See proposal.md. The combined phase owner must not treat the header-based relay helper as proof of the payload's lifecycle type.

## Goals / Non-Goals

**Goals:** Record pending created timing from the payload, including escaped JSON, while retaining the original relay result.

**Non-Goals:** Change forwarding classification, response ownership, or the ordinary token fast path.

## Decisions

- Keep observation in the existing total phase owner. Before bypassing parsing with created timing still pending, conservatively check for the literal `response.created` discriminator or a Unicode escape. A valid JSON encoding of that value must contain either the literal text or a Unicode escape; this avoids implementing another JSON parser.
- Parse candidates for observation and then return the unchanged relay decision. Ordinary frames without either candidate signal still bypass parsing. A false-positive candidate only performs an extra parse; it does not alter forwarding or timestamps.
- Extend the existing route table and owner sequence property. The latter generates literal, escaped-key, and escaped-value forms so representation cannot bypass the pending observation.

## Risks / Trade-offs

- Token text mentioning `response.created` or containing a Unicode escape can trigger parsing while created remains pending → retain unchanged relay results and existing parse-spy coverage for ordinary tokens. This conservative cost is required to distinguish an encoded pending lifecycle event from its header.

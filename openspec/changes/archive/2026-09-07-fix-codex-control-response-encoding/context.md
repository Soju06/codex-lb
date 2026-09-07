# Standalone search response encoding

## Evidence

Requests including `d7dc38a9-287e-40dc-96c7-892adae00aca` returned HTTP 200 on
2026-09-07 but Codex Desktop could not decode search JSON. A synthetic search
through the same account route with `Accept-Encoding: gzip` returned 34,459
bytes beginning `1f8b08`, `Content-Type: application/json`, and
`Content-Encoding: gzip`. The downstream adapter removed encoding while keeping
those gzip bytes. Gzip decompression produced `encrypted_output`, `output`, and
`results`. The same search with identity negotiation returned 69,378 bytes of
JSON and a nonempty output string.

The synthetic request shape follows the
[Codex search client](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/endpoint/search.rs):
`id`, `model`, and `commands.search_query`. No original customer search content
or credentials are recorded here.

## Decision and example

For control requests only, replace client compression preferences with identity
encoding. For example, a native `accept-encoding: gzip, br` becomes one
`accept-encoding: identity` at the same position. The response remains opaque
and the existing safe-header allowlist remains valid. The trade-off is more
upstream bandwidth for these small buffered responses.

## Verification boundary

HTTP 200 and readiness checks alone missed this issue. Regression and deployment
verification must decode the complete successful search JSON at the client side,
including a nonempty output and a results collection. Existing OpenSpec-wide
validation failures in unrelated specs are documented in the earlier v1 alias
change and are not part of this correction.

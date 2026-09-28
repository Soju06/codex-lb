# Scope and decision

The client may send `truncation: "auto"` on a continuation containing an
enforced-effort reset. Responses serialization already removes `truncation`
for every subscription send; rejecting it only because the proxy introduced
an update makes the anchored request fail before upstream can receive it.

For example, an API key enforcing low effort on a `previous_response_id`
continuation must still prepend a low-effort update when the client supplies
automatic truncation. The upstream payload contains the reset and user turn,
but not `truncation`. An automatic `context_management` compaction request
still fails when updates are present. An explicitly requested logprobs field
is not a local Astra policy capability decision; upstream remains responsible
for accepting or rejecting it.

This change does not change who owns the reset, the standalone compact
endpoint, generic `include` validation, or externally configured source
contracts.

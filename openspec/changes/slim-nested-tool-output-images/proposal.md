# Change: slim-nested-tool-output-images

## Why

Production incident (2026-07-15, dump
`20260715T074918.465731Z-websocket-gpt-5.6-sol-ws_937b8b45160f4ae18abd4ab7e6140dc3`):
a websocket `response.create` of 20,557,239 bytes was rejected with
`payload_too_large` (cap 15,728,640) even though 19,711,766 bytes of it were 51
historical inline images — exactly what the oversized-payload slimmer exists to
remove. The slimmer walked `message.content` parts and string-valued
`function_call_output.output`, but the images lived in **list-valued
`custom_tool_call_output.output`** (content parts produced by screenshot-style
tools), so slimming was a no-op (`20,557,239 → 20,557,239`, `summary=None`) and
the request failed after burning retries. Each retry also wrote a ~15MB payload
dump, accumulating 154 files / 1.1GB on the proxy host.

The existing requirement ("historical inline images MUST be replaced with
textual omission notices") already covers these images; the implementation
simply never visited them. `custom_tool_call_output` and
`apply_patch_call_output` string outputs were also excluded from the oversized
tool-output rule for no documented reason.

## What Changes

- Historical slimming inspects the `output` of all three tool-call output item
  types (`function_call_output`, `custom_tool_call_output`,
  `apply_patch_call_output`):
  - string `output` follows the existing oversized-tool-output omission rule
    (previously `function_call_output` only);
  - list/mapping `output` (content parts) is walked with the same inline-image
    replacement used for `message.content`, preserving non-image parts and
    item order/`call_id`/`status`.
- Both slimmer copies are fixed (`app/core/clients/proxy.py` websocket client
  path and `app/modules/proxy/_service/response_create.py` service path).
- No API surface, schema, or config changes; behavior only kicks in past the
  existing size guard.

## Verification

- Replaying the incident dump through the fixed slimmer:
  `20,557,239 → 848,992 bytes` (51 images slimmed), passes
  `ResponsesRequest` validation, latest-user-turn suffix untouched.
- Regression tests at the failing surface: websocket `stream_responses`
  integration test plus unit tests for nested-list outputs and
  custom/apply_patch string-output parity.

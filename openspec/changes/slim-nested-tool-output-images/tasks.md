# Tasks: slim-nested-tool-output-images

## 1. Implementation

- [x] 1.1 `app/core/clients/proxy.py` `_slim_historical_response_input_item`:
      handle all `_SLIMMABLE_TOOL_CALL_OUTPUT_ITEM_TYPES`; string outputs keep
      the oversized-omission rule, list/mapping outputs go through
      `_slim_historical_response_content` for inline-image replacement
- [x] 1.2 Same change in
      `app/modules/proxy/_service/response_create.py` (service copy, reuses
      `_PENDING_TOOL_CALL_OUTPUT_ITEM_TYPES`)

## 2. Regression coverage

- [x] 2.1 Unit: images nested in `custom_tool_call_output` /
      `function_call_output` list outputs are replaced with omission notice
      parts; surrounding text parts and the latest user turn are preserved
- [x] 2.2 Unit: oversized string outputs of `custom_tool_call_output` and
      `apply_patch_call_output` get the tool-output omission notice; `status`
      is preserved
- [x] 2.3 Integration (failing product path): websocket `stream_responses`
      with a historical `custom_tool_call_output` carrying an inline image
      slims below budget and completes instead of raising `payload_too_large`

## 3. Verification

- [x] 3.1 Replay incident dump through both slimmer copies:
      20,557,239 → 848,992 bytes, 51 images slimmed, schema-valid

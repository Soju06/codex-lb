# Design: keep-replayed-history-images-on-http-bridge

## Decision: which images keep a request off the bridge

The bypass predicate becomes:

```
bypass = image_generation_tool
      or any_external_image_url(input)            # whole input, any depth
      or any_input_image(current_turn(input))     # any form, incl. data:
```

`current_turn(input)` is the suffix of `input` after the last model-output item.
A model-output item is an assistant-role message or an item of type
`reasoning`, `function_call`, `custom_tool_call`, or `apply_patch_call`, the
same set `_http_bridge_input_item_type` already treats as response output. If
there is no model-output item, the whole input is the current turn.

### Alternatives considered

1. **Bypass only for external URLs and `image_generation`, with no
   current-turn clause.** This would bridge every inline image, including
   brand-new ones. It drops the #903 protection: an invalid new inline image
   would again hold a pending slot until local timeout. #2425 argues for this
   option; this change does not take it.
2. **Walk only the last user message.** This misses the most common
   image-bearing current turn in agent loops, a `function_call_output` that
   returns a screenshot right after the model's `function_call`.
3. **Chosen: current turn plus external URLs anywhere.** It keeps #903's
   fail-fast for every new image, closes the permanent-bypass bug for replayed
   history, and keeps the external-URL hazard at any depth.

## Constraints

- The external-URL walk MUST recurse the whole input and match schemes
  case-insensitively, reusing `_json_value_contains_external_input_image`. It
  MUST NOT use `_count_external_image_urls`, which reads only top-level items
  and one level of `content`.
- The upstream-transport pin (`_input_image_request_requires_http_upstream`) is
  unchanged. The bridge bypass and the transport pin stay separate decisions
  (#2386).
- A historical inline image that stays bridged still counts toward the payload
  size, so the existing `payload_size` bypass (frame budget) still sends an
  oversized replay to the raw path. The bridge MUST NOT strip or rewrite the
  image below that budget.
- Hot path: the image walks run on every bridge-path request. The
  implementation SHOULD walk the input once for model-output position and image
  presence, not once per clause.

## Oracles

Each named test is the oracle that stops a regression of its clause. The fail-on-base column states whether the test must fail on `09a140fa9`.

| Case | Test (to add) | Fails on base |
|---|---|---|
| (a) historical `data:` image in replayed `function_call_output` stays bridged | `tests/integration/test_http_promotion_accounting.py::test_replayed_history_inline_image_stays_on_http_bridge` (asserts no raw call, a bridge upstream `response.create` carrying the image byte-identical, and no `bypass/image` counter increment) | yes |
| (a′) current-turn image still bypasses (scenario "Nested input_image bypasses bridge") | `…::test_current_turn_image_still_bypasses_http_bridge` (user-message image and trailing `function_call_output` image) | no (guards #903) |
| (b) external URL still bypasses, including nested in history | `…::test_external_image_url_bypasses_http_bridge_even_in_history` (parametrized: current turn / historical tool output) | no (guard) |
| (c) `image_generation` still bypasses | `…::test_image_generation_tool_still_bypasses_http_bridge` | no (guard) |

Guards (a′), (b), and (c) must be proven by mutation: disabling each clause of
the predicate must fail its guard. The unit-level boundary is covered by
`tests/unit/test_proxy_utils.py` cases for `current_turn` on inputs with no
model-output item, a trailing `reasoning` item, and `custom_tool_call` /
`apply_patch_call` boundaries.

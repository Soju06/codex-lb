# Tasks: keep-replayed-history-images-on-http-bridge

## 1. Implementation

- [x] 1.1 `app/modules/proxy/_service/response_create.py`: add the bridge-bypass
  image predicate described in `design.md`. It MUST reuse
  `_json_value_contains_external_input_image` for the whole-input
  external-URL walk and MUST NOT use `_count_external_image_urls`. Expose it
  through the existing `service.py` / `service_stubs.py` façade seam, within the
  `service.py` line ratchet.
- [x] 1.2 `app/modules/proxy/_service/http_bridge/streaming.py`
  `_stream_http_bridge_or_retry`: replace `image_request` in the bypass
  condition with the new predicate. Keep `image_generation_request`, the
  `reason="image"` counter, and the log line. Leave
  `force_upstream_stream_transport` and its predicate unchanged.
- [x] 1.3 Leave the transport pin in `streaming/retry.py` unchanged. This change
  is bridge-only.
- [x] 1.4 `docs/routing.md` "HTTP to WebSocket promotion": say that only
  current-turn images, external image URLs, and image generation bypass the
  bridge.

## 2. Regression coverage

- [x] 2.1 Add the four integration oracles named in `design.md` (a, a′, b, c).
- [x] 2.2 Update `tests/integration/test_http_promotion_accounting.py::test_historical_image_does_not_pin_later_turns_to_http`: its `assert not upstreams` asserts the superseded bypass for a history-only image and MUST now assert the request is bridged. Add unit cases for the current-turn boundary in
  `tests/unit/test_proxy_utils.py`. Update
  `test_stream_http_bridge_or_retry_bypasses_bridge_for_input_image` so that it
  uses a current-turn image.
  The existing unit bypass fixture already supplies a current-turn user image.
- [ ] 2.3 Prove (a) fails on `09a140fa9` with only the `app/` diff
  reverse-applied, and passes with it. Mutation-prove guards a′, b, and c.
  Focused runtime mutation proves the baseline whole-input predicate fails (a)
  and disabling each guard fails a′, b, and c. Exact app-diff reverse application
  remains for the lead's isolated baseline verification.

## 3. Spec hygiene

- [ ] 3.1 Before this change is archived, archive (or rebase)
  `narrow-input-image-upstream-transport-pin` so that its stale `MODIFIED`
  delta of the same requirement cannot sync the old "any `input_image`" trigger
  back. Record the order in the PR body.
- [ ] 3.2 On sync, replace the requirement in place in
  `openspec/specs/responses-api-compat/spec.md`. Do not append a dated
  correction. Move the stable part of `context.md` into the capability
  `context.md` section that already describes the image bypass.
- [ ] 3.3 `npx --yes @fission-ai/openspec@1.11.0 validate keep-replayed-history-images-on-http-bridge --strict`
  and `… validate --specs`.

# Tasks: adapt-subscription-prompt-cache-controls

## 1. Implementation

- [x] 1.1 Strip public explicit prompt-cache controls only from subscription
      Responses egress while preserving `prompt_cache_key` and prompt content
- [x] 1.2 Report successful subscription fallback through
      `X-Codex-LB-Prompt-Cache-Mode: subscription-implicit`
- [x] 1.3 Preserve explicit controls on OpenAI-compatible model-source egress

## 2. Regression coverage

- [x] 2.1 Exercise the exact `/v1/responses` subscription request shape and
      assert upstream serialization plus response header
- [x] 2.2 Exercise the model-source route as a negative control and assert the
      controls remain intact

## 3. Verification

- [x] 3.1 Run focused unit/integration tests and strict OpenSpec validation
- [ ] 3.2 Re-run the bounded live request against the overlay-preserving stack

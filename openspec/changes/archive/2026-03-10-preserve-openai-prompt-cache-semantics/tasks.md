## 1. Request Normalization

- [x] 1.1 Keep `prompt_cache_retention` compatibility-safe by continuing to strip it while still stripping `temperature`
- [x] 1.2 Normalize `promptCacheKey` and `promptCacheRetention` into canonical handling for compatibility paths

## 2. Routing Affinity

- [x] 2.1 Add a dedicated OpenAI cache-affinity path so `/v1/responses` and `/v1/responses/compact` pin upstream account selection by `prompt_cache_key`
- [x] 2.2 Apply the same OpenAI cache-affinity behavior to `/v1/chat/completions` after Chat-to-Responses mapping

## 3. Regression Coverage

- [x] 3.1 Update unit tests for payload normalization and alias handling
- [x] 3.2 Add integration tests for `/v1` prompt-cache affinity across streaming and compact routes
- [x] 3.3 Add chat-mapping regression coverage for preserved prompt cache controls
- [x] 3.4 Run `openspec validate --specs` and the targeted pytest suites

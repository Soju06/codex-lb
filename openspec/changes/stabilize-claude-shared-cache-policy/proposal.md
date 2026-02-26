## Why

Claude harness traffic in `codex-lb` intermittently misses prompt cache hits because cache key selection may vary across turns when the source is `metadata` or `cache_control`.
Even with deterministic derivation, real clients often send volatile metadata or changing block-level markers.
This causes unstable sticky routing and lower cache reuse.

## What Changes

- Change Claude prompt cache policy so only `explicit` caller-provided keys are preserved as-is.
- Force all non-explicit sources (`metadata`, `cache_control`, `anchor`, `none`) into a deterministic `claude-shared:*` key lane.
- Keep the existing dedicated `:count_tokens` lane behavior unchanged.
- Map Anthropic reasoning aliases (`reasoningEffort`, `reasoning.effort`) into `ResponsesRequest.reasoning.effort`.
- Update Anthropic compatibility tests to assert the new stable-key contract.

## Capabilities

### Added Capabilities

- `anthropic-compat`: stable Claude shared prompt cache key policy for non-explicit sources.

## Impact

- **Code**: `app/modules/anthropic_compat/api.py`, `app/modules/anthropic_compat/service.py`, `app/modules/anthropic_compat/translator.py`
- **Tests**: `tests/integration/test_anthropic_compat.py`, `tests/unit/test_anthropic_translator.py`

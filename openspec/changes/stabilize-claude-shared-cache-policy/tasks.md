## 1. Claude Prompt Cache Policy

- [x] 1.1 Preserve prompt cache key only when source is `explicit`
- [x] 1.2 Force non-explicit sources (`metadata`, `cache_control`, `anchor`, `none`) to `claude-shared:*`
- [x] 1.3 Keep `:count_tokens` lane behavior unchanged

## 2. Tests

- [x] 2.1 Update integration test expectations for Claude requests with `cache_control`
- [x] 2.2 Keep explicit-key preservation regression test passing

## 3. Spec Delta

- [x] 3.1 Add anthropic-compat requirement delta for stable non-explicit Claude cache keys
- [ ] 3.2 Validate specs locally with `openspec validate --specs` (CLI `openspec` unavailable in this environment)

## 4. PR Review Alignment

- [x] 4.1 Remove hardcoded Claude model forcing from anthropic-compatible API transport path
- [x] 4.2 Move API key reservation enforcement/release orchestration from API route layer into anthropic service layer
- [x] 4.3 Remove out-of-band implementation note file (`info.md`) per review guidance

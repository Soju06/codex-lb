## MODIFIED Requirements

### Requirement: Additional usage persistence normalizes upstream aliases to canonical quota keys
Persisted additional-usage rows MUST record one internal canonical `quota_key` even when upstream changes raw `limit_name` or `metered_feature` aliases.

#### Scenario: Refresh coalesces mixed aliases for one canonical quota before pruning
- **GIVEN** one refresh payload includes multiple `additional_rate_limits` items that resolve to the same canonical `quota_key`
- **AND** at least one alias reports usable window data while another alias for that same `quota_key` reports `rate_limit = null`
- **WHEN** the refresh persists additional usage
- **THEN** it merges all aliases by canonical `quota_key` before deleting stale rows
- **AND** persisted rows for the usable window remain available for later gated-model selection

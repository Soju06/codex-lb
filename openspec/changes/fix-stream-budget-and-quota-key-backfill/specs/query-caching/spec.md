## MODIFIED Requirements

### Requirement: Additional usage persistence normalizes upstream aliases to canonical quota keys
Persisted additional-usage rows MUST record one internal canonical `quota_key` even when upstream changes raw `limit_name` or `metered_feature` aliases.

#### Scenario: Legacy stored quota keys remain readable under the current canonical key
- **GIVEN** the registry renames a canonical additional-usage `quota_key`
- **AND** it lists the previous durable key as a legacy `quota_key` alias for that same quota family
- **WHEN** selection, dashboard, or cleanup code reads or deletes persisted rows for the current canonical key
- **THEN** rows stored under the legacy `quota_key` remain readable through the current canonical key
- **AND** canonical list/read results surface the current key instead of the legacy durable alias

#### Scenario: Refresh coalesces mixed aliases for one canonical quota before pruning
- **GIVEN** one refresh payload includes multiple `additional_rate_limits` items that resolve to the same canonical `quota_key`
- **AND** at least one alias reports usable window data while another alias for that same `quota_key` reports `rate_limit = null`
- **WHEN** the refresh persists additional usage
- **THEN** it merges all aliases by canonical `quota_key` before deleting stale rows
- **AND** persisted rows for the usable window remain available for later gated-model selection

#### Scenario: Historical rows remain readable after canonical key rename
- **GIVEN** persisted `additional_usage_history` rows were written under an earlier canonical `quota_key`
- **AND** the current registry still recognizes the same raw upstream aliases for that quota family
- **WHEN** selection or dashboard queries request the current canonical `quota_key`
- **THEN** repository reads match both the current `quota_key` and the known raw alias fields
- **AND** the historical rows remain visible until refresh rewrites them under the newer canonical key

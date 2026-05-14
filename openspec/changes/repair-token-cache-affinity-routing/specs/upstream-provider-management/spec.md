## MODIFIED Requirements

### Requirement: Platform fallback uses the remaining percentages visible to operators
For phase-1 fallback, the service MUST treat a compatible ChatGPT-web candidate as healthy only while it remains selectable for the request and both persisted usage snapshots required for fallback evaluation are present with `primary_remaining_percent > 10` and `secondary_remaining_percent > 5`. Compatible candidates with either snapshot missing MUST NOT count as healthy for suppressing Platform fallback. Candidates that are still rate-limited, quota-blocked, paused, or deactivated MUST NOT suppress Platform fallback based on persisted remaining percentages alone. A durable backend Codex `codex_session` affinity MAY still suppress Platform fallback for its pinned ChatGPT-web account when that pinned target becomes selectable within the sticky grace window and remains above the same remaining-percent thresholds at that grace-window selection point. A fresh provider-scoped `prompt_cache` mapping to `openai_platform` MAY continue a prior Platform fallback selection for the same stateless prompt-cache key within the configured freshness window. This continuation MUST NOT be treated as equal-weight Platform routing and MUST NOT be persisted or interpreted as durable `codex_session` continuity. When no compatible ChatGPT-web candidate remains positively healthy under those thresholds, the service MAY consider `openai_platform` as fallback, subject to the existing route-family eligibility checks.

#### Scenario: Fresh Platform prompt-cache mapping continues fallback locality
- **WHEN** a request targets an eligible stateless Platform fallback route family
- **AND** a fresh provider-scoped `prompt_cache` mapping exists for the request key and points to an eligible `openai_platform` routing target
- **AND** the ChatGPT-web pool has since recovered above the fallback thresholds
- **THEN** the service MAY continue routing that prompt-cache key to the mapped Platform target until the mapping expires
- **AND** unrelated prompt-cache keys still evaluate ChatGPT-web health normally

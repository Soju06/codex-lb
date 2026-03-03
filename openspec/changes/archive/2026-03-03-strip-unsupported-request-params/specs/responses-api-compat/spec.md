## ADDED Requirements

### Requirement: Strip known unsupported advisory parameters before upstream forwarding
Before forwarding Responses payloads upstream, the service MUST remove known unsupported advisory parameters that upstream rejects with `unknown_parameter`. At minimum, the service MUST strip `prompt_cache_retention` and `temperature` from normalized payloads for both standard and compact Responses endpoints.

#### Scenario: prompt_cache_retention provided
- **WHEN** a client sends a valid Responses request that includes `prompt_cache_retention`
- **THEN** the service accepts the request and forwards payload without `prompt_cache_retention`

#### Scenario: temperature provided
- **WHEN** a client sends a valid Responses or Chat-mapped request that includes `temperature`
- **THEN** the service accepts the request and forwards payload without `temperature`

#### Scenario: unrelated extra field provided
- **WHEN** a client sends a valid request with an unrelated extra field not in the unsupported list
- **THEN** the service preserves that field in forwarded payload

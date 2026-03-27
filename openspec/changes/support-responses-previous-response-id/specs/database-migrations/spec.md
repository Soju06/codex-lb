### ADDED Requirement: Durable response snapshot continuity storage
Startup migrations SHALL create and preserve the durable storage needed to replay `previous_response_id` across bridge loss and restart. The continuity schema SHALL include caller scoping so one API key cannot replay another caller's stored response chain.

#### Scenario: startup migration creates response snapshot storage
- **WHEN** startup migrations upgrade a database without response continuity storage
- **THEN** the schema includes `response_snapshots`
- **AND** that table includes `api_key_id` alongside the serialized continuity payload columns

#### Scenario: startup migration repairs partial response snapshot storage
- **WHEN** startup migrations encounter an existing `response_snapshots` table missing `api_key_id` or the parent/created-at continuity index
- **THEN** the migration adds the missing column and index without requiring operator intervention

## ADDED Requirements

### Requirement: Direct Docker respects addressable worker-pool startup

The direct Docker helper MUST rebuild the current checkout and recreate the local container without forcing the runtime worker count to one. If the env file configures multiple workers while the HTTP responses session bridge is enabled, the image startup path MUST use the addressable bridge worker pool rather than a plain multi-worker Uvicorn process.

#### Scenario: direct Docker uses env-file worker count

- **WHEN** an operator runs the local Docker helper
- **AND** `.env.local` configures `CODEX_LB_UVICORN_WORKERS` greater than one
- **THEN** the helper does not add a conflicting `CODEX_LB_UVICORN_WORKERS=1` override
- **AND** the container startup path is responsible for selecting the safe worker-pool runtime

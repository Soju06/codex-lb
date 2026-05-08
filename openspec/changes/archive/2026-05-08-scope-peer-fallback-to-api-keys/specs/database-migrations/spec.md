## ADDED Requirements

### Requirement: API key peer fallback URLs are migrated

The system SHALL add persistent API key peer fallback URL storage through Alembic so each API key can be associated with zero or more peer codex-lb base URLs.

#### Scenario: Migration creates API key peer fallback URL storage

- **WHEN** the database is upgraded to the migration containing API key peer fallback URL storage
- **THEN** the schema contains an `api_key_peer_fallback_urls` table linking API keys to peer fallback base URLs
- **AND** deleting an API key removes its peer fallback URL rows

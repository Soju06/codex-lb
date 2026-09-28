## ADDED Requirements

### Requirement: Per-key automatic Astra notes preference
API-key create, update, list, and detail responses SHALL expose `auto_enable_astra_notes` using the existing dashboard field alias convention. Its persisted value SHALL default to false for new and migrated keys. An omitted or null update SHALL leave the stored value unchanged; explicit false SHALL disable the override. Existing key-management permissions SHALL apply. The existing create and edit dialogs SHALL offer this preference with an action-focused description.

#### Scenario: Existing key opts in and out
- **GIVEN** an existing key with the default false value
- **WHEN** its owner or an authorized administrator enables the preference and later disables it
- **THEN** each saved value is returned by subsequent key reads and used after key-cache invalidation
- **AND** an unrelated edit does not reset the preference

### Requirement: Native catalog carries the per-key Astra activation defaults
For an authenticated opted-in key, the native Codex model catalog SHALL set `supports_experimental_context`, `model_messages.token_budget.enabled`, and `model_messages.token_budget.use_history_notes_extension` to true only for an allowed, visible native `gpt-6-astra` entry with existing complete token-budget metadata. The proxy SHALL preserve all other metadata and SHALL NOT mutate the shared registry, add a missing model, expose a hidden model, apply the override to external source models, or alter generic OpenAI model lists. Native catalogs SHALL be marked private and requiring revalidation.

#### Scenario: Two keys discover models
- **GIVEN** two keys with access to Astra, Sol, and Luna, only one of which opts in
- **WHEN** each calls the native models route or `/v1/models?client_version=...`
- **THEN** only the opted-in key receives the three Astra activation overrides
- **AND** Sol, Luna, and the other key retain the upstream metadata

#### Scenario: Astra is unavailable or has incomplete metadata
- **WHEN** an opted-in key has no allowed visible native Astra entry or that entry lacks complete token-budget metadata
- **THEN** the proxy leaves the catalog unchanged rather than inventing a model or incomplete activation defaults

### Requirement: Automatic activation preserves client and session control
Disabling the preference SHALL stop applying its catalog override without deleting notes, changing context endpoint authorization, or modifying running sessions. Documentation SHALL explain that the preference requires compatible remote discovery, respects explicit client configuration, and takes effect after catalog refresh for new conversations.

#### Scenario: User turns off automatic activation
- **WHEN** a key disables the preference
- **THEN** subsequent catalog responses use unmodified upstream defaults
- **AND** existing notes and context operations retain their existing ownership and authorization rules


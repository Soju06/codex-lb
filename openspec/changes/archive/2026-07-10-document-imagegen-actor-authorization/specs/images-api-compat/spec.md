## ADDED Requirements

### Requirement: Codex client setup enables built-in image generation

The project SHALL document every complete `[model_providers.codex-lb]` example
with a non-empty static `http_headers` entry mapping
`x-openai-actor-authorization` to `codex-lb`. The setup guidance MUST explain
that Codex uses this marker to enable its built-in image-generation gateway for
the custom provider, that the marker is not a credential, and that it does not
replace codex-lb Bearer authentication when API-key auth is enabled.

#### Scenario: User configures the standard Codex provider

- **WHEN** a user follows the documented Codex CLI or IDE provider setup
- **THEN** the resulting `codex-lb` provider includes
  `http_headers = { "x-openai-actor-authorization" = "codex-lb" }`
- **AND** a new Codex session can expose the built-in `imagegen` tool and send
  image requests to the Codex-base image routes

#### Scenario: User configures codex-lb API-key authentication

- **WHEN** a user follows the documented setup with `env_key = "CODEX_LB_API_KEY"`
- **THEN** the same actor-authorization marker remains in the provider table
- **AND** the user is told that Bearer authentication is still enforced
  independently by codex-lb

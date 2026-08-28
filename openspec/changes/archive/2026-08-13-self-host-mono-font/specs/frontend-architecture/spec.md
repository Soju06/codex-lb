# frontend-architecture Delta

## ADDED Requirements

### Requirement: Dashboard assets are fully self-hosted

The dashboard MUST NOT load fonts or other render-blocking resources from external origins; all font assets ship with the build and declare `font-display: swap`.

#### Scenario: No external origins in the built shell

- **WHEN** the dashboard shell is built
- **THEN** `index.html` and the emitted assets reference no external font or stylesheet origins

#### Scenario: First paint proceeds without network egress

- **GIVEN** a deployment without outbound internet access
- **WHEN** an operator loads the dashboard
- **THEN** first paint is not blocked on any external request and monospace text renders via the bundled font or the system fallback

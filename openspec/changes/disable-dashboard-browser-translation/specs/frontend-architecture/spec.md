## MODIFIED Requirements

### Requirement: Dashboard tolerates browser translation DOM mutation

The dashboard HTML shell SHALL allow browser/extension translation while protecting React reconciliation from external DOM node moves.

#### Scenario: Dashboard permits browser translation

- **WHEN** the browser loads the dashboard HTML shell
- **THEN** the document, body, and React root do not opt out of browser translation

#### Scenario: Dashboard tolerates externally moved React nodes

- **WHEN** an extension moves a React-owned DOM node before React removes or inserts around it
- **THEN** the dashboard startup guard logs the external mutation
- **AND** the guarded DOM operation returns without throwing a reconciliation-stopping exception

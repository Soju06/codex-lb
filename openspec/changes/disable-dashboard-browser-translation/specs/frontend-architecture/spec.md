## MODIFIED Requirements

### Requirement: Dashboard HTML shell avoids browser translation DOM mutation

The dashboard HTML shell SHALL opt out of browser/extension translation for the React-owned document surface so external translation tools do not inject markup into React-managed text nodes.

#### Scenario: Dashboard opts out of Google Translate DOM rewriting

- **WHEN** the browser loads the dashboard HTML shell
- **THEN** the document advertises `notranslate` metadata for Google Translate
- **AND** the document, body, and React root are marked with `translate="no"`

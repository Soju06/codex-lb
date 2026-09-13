## MODIFIED Requirements

### Requirement: ProxyService remains a stable façade

`app.modules.proxy.service.ProxyService` and the required compatibility exports SHALL remain
available to existing consumers. Behavior extracted from
`ProxyService` or `service.py` SHALL be owned by focused private modules under
`app/modules/proxy/_service/`.
Private service domains SHALL comply with the repository's explicit cross-domain
dependency policy. The proxy package SHALL NOT carry re-export-only
compatibility shim modules that have no importers.

#### Scenario: Existing consumers import the proxy façade

- **WHEN** an existing caller imports `ProxyService` or a required compatibility export from `app.modules.proxy.service`
- **THEN** the import resolves to behavior compatible with the pre-change façade
- **AND** no caller migration is required

#### Scenario: Importer-less compatibility shim is removed

- **WHEN** a private re-export-only module under `app/modules/proxy/` has no importers in `app/`, `tests/`, or `scripts/`
- **THEN** the module is deleted rather than retained
- **AND** the architecture check has no rule that requires the deleted module to exist

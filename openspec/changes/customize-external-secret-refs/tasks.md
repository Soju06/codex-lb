# Tasks: customize-external-secret-refs

## 1. Helm contract

- [x] 1.1 Render chart-managed ExternalSecret resources with the stable API
- [x] 1.2 Add independent remote key and optional property values for both required credentials
- [x] 1.3 Preserve the existing remote secret layout as the zero-config default
- [x] 1.4 Render the default layout when remote reference overrides are explicitly nulled

## 2. Documentation and verification

- [x] 2.1 Document an individual-secret Infisical example
- [x] 2.2 Add rendering coverage for defaults and property-free absolute keys
- [x] 2.3 Run focused Helm tests and strict OpenSpec validation

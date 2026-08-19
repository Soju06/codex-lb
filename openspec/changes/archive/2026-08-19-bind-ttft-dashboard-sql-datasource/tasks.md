## 1. Regression coverage

- [x] 1.1 Add dashboard JSON assertions for the visible single-select
      PostgreSQL `DS_SQL` variable and all four typed panel bindings.
- [x] 1.2 Add a rendered ConfigMap assertion proving Helm preserves the
      runtime datasource contract.

## 2. Dashboard and operator documentation

- [x] 2.1 Declare `DS_SQL` and convert all TTFT panels to typed PostgreSQL
      datasource objects without changing SQL or layout.
- [x] 2.2 Document Grafana-side PostgreSQL datasource selection while
      preserving the existing sidecar deployment model.

## 3. Verification

- [x] 3.1 Capture focused RED, implement the minimal artifact fix, and run the
      focused tests GREEN.
- [x] 3.2 Run changed-file diagnostics, Ruff, typecheck, Helm architecture and
      rendering gates, strict affected OpenSpec validation, and final diff
      review.
- [x] 3.3 Provision the exact rendered dashboard into isolated Grafana 12.4.4
      with a synthetic PostgreSQL datasource; inspect the API and 1440x900
      browser surface and capture evidence.
- [x] 3.4 Sync the verified delta into the owning capability and archive the
      completed change before publication.

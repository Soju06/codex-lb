## 1. Regression Coverage

- [x] 1.1 Add a failing real Helm rendering regression that compares the custom external database port in the generated URL and NetworkPolicy egress rule.
- [x] 1.2 Retain a rendered default-port control and bundled PostgreSQL policy coverage.

## 2. Template Fix

- [x] 2.1 Render `externalDatabase.port` with its 5432 default in the external PostgreSQL egress branch without changing selectors or bundled mode.

## 3. Verification

- [x] 3.1 Run focused Helm tests, custom/default manual renders, Python lint, template/YAML checks, and strict OpenSpec validation.
- [x] 3.2 Review the committed diff for application/migration selector coverage, internal/external mode separation, and default behavior.

## 4. Direct URL Review Repair

- [x] 4.1 Add failing real Helm regressions for a direct URL with a custom port and a no-port precedence control.
- [x] 4.2 Derive NetworkPolicy egress from an explicit direct-URL port or PostgreSQL's 5432 default.
- [x] 4.3 Re-run focused Helm, lint, rendered surface, and strict OpenSpec verification.

## 5. Equivalent Direct URL Paths

- [x] 5.1 Add failing rendered regressions for source precedence, query ports, and leading-zero normalization.
- [x] 5.2 Match chart database-source precedence and normalize the effective direct URL port.
- [x] 5.3 Re-run focused tests, template checks, strict OpenSpec validation, and committed-diff review.

## 6. Advanced Direct URL Forms

- [x] 6.1 Add failing rendered regressions for encoded query ports, portless IPv6, multihost ports, and invalid query ports.
- [x] 6.2 Render a unique validated port set matching direct URL failover semantics.
- [x] 6.3 Re-run focused verification and committed-diff review.
- [x] 6.4 Add rendered regressions for authority fallback, blank query items, and encoded numeric whitespace found by committed review.
- [x] 6.5 Match SQLAlchemy authority fallback and blank-item semantics.

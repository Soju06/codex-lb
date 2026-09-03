## 1. Policy and Implementation

- [x] 1.1 Validate the OpenSpec change and confirm the delta replaces the full owning requirement.
- [x] 1.2 Remove HTTP-status-only deactivation from the shared usage-error classifier while preserving permanent-code and explicit-message handling.
- [x] 1.3 Audit all scoped usage-404 references and document whether any concrete account-not-found or payment-required terminal envelope exists.

## 2. Regression Coverage

- [x] 2.1 Invert the existing bare 402 behavior test and add bare 404 coverage for unchanged status, no persistence update, no routing-unavailable mark, and retained refresh-failure logging.
- [x] 2.2 Preserve regression coverage for permanent-failure status mapping and explicit deactivation-message handling.
- [x] 2.3 Cover the post-auth-refresh retry path with an ambiguous HTTP error.

## 3. Documentation

- [x] 3.1 Review published usage-refresh documentation and update it only if it currently describes failure/deactivation semantics.

## 4. Verification

- [x] 4.1 Run the focused usage-updater tests and the full unit suite.
- [x] 4.2 Run lint and type-check gates, restoring `uv.lock` if the type checker mutates it.
- [x] 4.3 Run strict change validation and main-spec validation, then verify implementation coherence against the change.

## 5. Delivery

- [x] 5.1 Write `/tmp/usage404-report.md` with the capability choice, diff stat, reference audit, evidence conclusion, gate outputs, and explicit non-goals.
- [x] 5.2 Commit the verified change on the feature branch with a Conventional Commit message.

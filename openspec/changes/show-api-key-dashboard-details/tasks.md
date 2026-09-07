## 1. Backend profile contract

- [x] 1.1 Add the strict privacy-safe profile response schema and service mapper; verify type/lint checks accept the explicit allowlist.
- [x] 1.2 Add the mandatory Bearer-authenticated profile route; verify integration tests cover the exact response keys and 401 behavior without sensitive identifiers.

## 2. Frontend data and credential lifecycle

- [x] 2.1 Add strict profile and usage-limit schemas and load profile, usage, and logs concurrently; verify frontend schema/integration tests parse the complete snapshot.
- [x] 2.2 Add opt-in browser persistence, automatic restore, and removal on invalid authentication or Disconnect; verify integration tests cover persistence only after success and cleanup paths.

## 3. Frontend presentation

- [x] 3.1 Render API key identity, lifecycle, effective policies, and limit consumption with localized labels; verify the integration flow shows safe detail fields and omits sensitive fields.
- [x] 3.2 Apply distinct semantic summary accents and key-dashboard-specific balanced request-grid widths; verify component tests and a production frontend build succeed.

## 4. Specification and validation

- [x] 4.1 Sync the completed behavior into the main API key dashboard spec and context; verify the capability documentation matches the shipped privacy and persistence constraints.
- [x] 4.2 Run targeted backend/frontend tests, lint/type/build checks, and strict OpenSpec validation; record or resolve every failure before verification.

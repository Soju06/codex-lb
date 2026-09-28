## 1. Preserve public HTTPS

- [x] 1.1 Accept the HTTPS-only export hint and verify endpoint tests cover every platform, invalid hints, authority preservation and absent-hint compatibility.
- [x] 1.2 Include the browser protocol in script fetch and terminal URL generation; verify frontend installer and dashboard integration tests.

## 2. Fix catalog access

- [x] 2.1 Identify Python and PowerShell requests explicitly and sanitize HTTP 403 diagnostics; verify exported scripts against an edge-signature filter and forbidden/redirect failures.
- [x] 2.2 Verify the authenticated export-to-execution route with the edge filter and existing alias metadata assertions.

## 3. Verify and archive

- [x] 3.1 Run focused Python/frontend tests, lint and strict OpenSpec validation; record the results and any platform limitations.
- [x] 3.2 Sync the spec and stable context, verify implementation against scenarios, and archive the completed change.

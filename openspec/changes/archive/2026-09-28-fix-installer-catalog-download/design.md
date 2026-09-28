## Context

See proposal.md for the failing setup flow. HAProxy currently normalizes the projected scheme to HTTP. The application already centralizes trusted proxy projection. Cloudflare rejects Python's generic signature while accepting a named installer signature.

## Goals / Non-Goals

**Goals:** Repair HTTPS export and authenticated catalog downloads for Bash and PowerShell with the existing dependency footprint.

**Non-Goals:** Change ingress trust, Cloudflare rules, global scheme handling, the installation layout, or the unrelated active one-liner change.

## Decisions

- Add only an HTTPS upgrade hint to installer export. The dashboard knows its public protocol and supplies it to both download paths. The API uses the existing request base URL and changes only its scheme. Arbitrary origin overrides could move credentials to a different authority; blindly trusting forwarded headers would widen an unrelated security boundary.
- Keep absent-hint behavior for old API callers and local HTTP environments. HTTP is not an accepted hint because it could downgrade a correctly projected HTTPS URL.
- Set `codex-lb-installer/1.0` explicitly in Python and PowerShell. Keep standard-library clients, certificate validation, timeout and redirect rejection. This identifies the application honestly without impersonating a browser or requiring curl for downloaded Unix scripts.
- Handle HTTP 403 with sanitized guidance for edge access failures; do not echo server error payloads. Other failures retain existing diagnostics.
- Cover the public export endpoint, generated script execution against a local edge stub, and frontend download and terminal URL generation.

## Risks / Trade-offs

- Previously copied commands lack the hint and older saved scripts retain their old signature → after rollout users re-export from the refreshed Install tab.
- An administrator can still block the explicit signature → retain a clear forbidden error and preserve original files.
- Linux PowerShell can verify catalog execution, but native Windows ACL behavior still requires a Windows host → keep ACL logic unchanged.

## Migration Plan

No database or settings migration. A separately authorized application deployment and dashboard refresh publish the fix. Re-export scripts after deployment; no live runtime mutation is part of implementation.

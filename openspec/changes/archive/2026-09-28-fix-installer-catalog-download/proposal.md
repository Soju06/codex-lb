## Why

An installer downloaded successfully over HTTPS can fail its catalog request with HTTP 403: the HA proxy exports an HTTP scheme and Cloudflare rejects the default Python user agent. Valid keys therefore cannot finish client setup.

## What Changes

- Allow an optional `scheme=https` export hint to preserve HTTPS when the internal proxy reports HTTP; retain the request authority and path.
- Include the hint in HTTPS dashboard script downloads and terminal commands.
- Give catalog requests an explicit installer user agent on every platform and distinguish HTTP 403 from invalid credentials without printing response bodies.
- Preserve redirect rejection, private backups, and unchanged client files on download failure.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-key-dashboard`: HTTPS installer export and catalog download compatibility with edge browser-signature filters.

## Impact

Installer export API, generated Bash/PowerShell catalog requests, dashboard request URL generation, and focused regression tests. No new dependencies, settings, proxy trust changes, or production runtime changes.

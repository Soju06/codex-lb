## Why

Peer fallback base URL validators rely on `urlparse().params`, which only reports params from the final path segment. URLs such as `https://peer.example/a;b/c` can pass validation even though peer fallback base URLs disallow params.

## What Changes

- Reject semicolon path params anywhere in API key peer fallback base URLs.
- Reject semicolon path params anywhere in dashboard-managed peer fallback target URLs.
- Keep query and fragment rejection explicit, including empty query or fragment delimiters.

## Impact

- API keys: invalid peer fallback base URLs are rejected before persistence.
- Peer fallback targets: invalid dashboard target URLs are rejected before persistence.
- Tests: add integration coverage for semicolon path params and bare query delimiters.

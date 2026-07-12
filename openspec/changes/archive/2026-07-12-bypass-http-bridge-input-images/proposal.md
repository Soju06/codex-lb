## Why

Inline `input_image` requests can expose upstream validation errors that the
HTTP responses bridge cannot reliably surface before its response-created gate
or request budget expires. During beta live validation, a tiny inline PNG was
rejected quickly on the raw HTTP path but held a bridge pending slot until local
timeouts on the WebSocket bridge path.

## What Changes

- Bypass the HTTP responses bridge for Responses requests containing any
  `input_image` part, sending those requests over the existing raw HTTP stream
  path instead
- Preserve the existing unsupported uploaded-image rejection for
  `input_image.file_id` and `sediment://` references before forwarding
- Keep bridge behavior unchanged for text-only and file-only Responses requests

## Impact

- Valid inline images continue to work while using upstream HTTP error semantics
  for invalid image payloads
- Invalid inline images fail fast instead of occupying bridge pending slots until
  timeout
- Image requests lose HTTP bridge prompt-cache/session reuse, intentionally
  favoring correctness over cache affinity for this media path

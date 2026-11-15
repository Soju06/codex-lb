# Diagnose WebSocket connect fallback

## Provenance
Local addition in the fix/python-image-bridge-consolidation branch
(connect_diagnostics.py); see
allow-bounded-inline-images-on-bridge/consolidation.md.

Expose bounded, credential-safe original exception metadata before an already-approved bridge HTTP fallback. No routing, retry, timeout or settlement changes. Required to distinguish primary TLS errors from secondary cleanup exceptions.

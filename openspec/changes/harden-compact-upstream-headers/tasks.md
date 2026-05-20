## 1. Implementation

- [x] 1.1 Strip inbound body-framing and hop-by-hop headers before proxy forwarding.
- [x] 1.2 Use a compact-specific upstream header builder that only sends internal auth, JSON accept/content type, and selected account identity.

## 2. Verification

- [x] 2.1 Add unit coverage for compact upstream header construction and inbound header filtering.
- [x] 2.2 Add integration coverage for compact route header filtering before service execution.
- [x] 2.3 Run targeted pytest and OpenSpec validation.
- [x] 2.4 Deploy the updated service and prove the live compact endpoint no longer returns the Cloudflare `400` for a minimal compact request.

## Why

The local Codex image-generation request was rejected by HTTP ingress before historical image slimming because it exceeded 128 MiB. This fork needs a bounded 256 MiB Responses ingress budget.

## What Changes

- Raise the shared fixed Responses HTTP and default downstream websocket budget to 256 MiB.
- Retain the 32 MiB general budget, raw/decompressed checks, existing upstream limits, and removal of the old environment settings.
- Align Compose, regression tests, and owning specs with the fork default.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: larger fixed Responses HTTP and default downstream websocket ingress.
- `http-ingress-limits`: reuse the 256 MiB owning Responses budget.
- `deployment-installation`: document the fork's fixed budget.

## Impact

One shared constant, Compose launcher default, tests and documentation. No migration or new setting. Larger bodies can use more memory; upstream acceptance is still bounded independently.

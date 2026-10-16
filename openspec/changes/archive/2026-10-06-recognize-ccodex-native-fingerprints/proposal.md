# Recognize CCodex Native Fingerprints

## Why

CCodex relays requests from the stock Codex app server with dedicated originator values and User-Agent product prefixes. The proxy currently classifies those requests as non-native and rewrites their Codex client fingerprint, which can cause upstream GPT-6 requests to be rejected.

## What Changes

- Recognize only `ccodex-internal` and `ccodex-handoff-worker` and their matching User-Agent prefixes for upstream fingerprint preservation.
- Keep unlisted CCodex-like identities on existing non-native normalization and leave transport selection unchanged.
- Address only the fingerprint-recognition request from issue #2560. Stable release delivery from #2174 and a distinct upstream error code remain out of scope.

## Capabilities

- `responses-api-compat`: recognize the two reported CCodex originators and User-Agent prefixes as native while preserving non-native fingerprint normalization for unlisted clients.

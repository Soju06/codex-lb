## Why

`index.html` loaded JetBrains Mono from Google Fonts with a render-blocking stylesheet: every first paint waited on fonts.googleapis.com, and on air-gapped or egress-restricted deployments (a common shape for this product) first paint stalled until the request timed out. Geist Sans was already self-hosted; the mono font was the one remaining external request.

## What Changes

- JetBrains Mono (variable, weights 400–500, latin + latin-ext subsets, ~43 KB total) ships in `frontend/public/fonts/` with `@font-face` + `font-display: swap` declarations mirroring the existing Geist Sans pattern; the Google Fonts `<link>`/preconnects are removed.
- Non-latin glyphs fall back to the existing `ui-monospace, monospace` stack (previously they loaded extra remote subsets).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `frontend-architecture`: the dashboard MUST NOT reference external origins for fonts or other render-blocking resources.

## Impact

`frontend/index.html`, `frontend/src/index.css`, `frontend/public/fonts/*`. Built output verified free of googleapis/gstatic references.

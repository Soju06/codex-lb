## Why

Responses validation and internal forwarding can change full-resend classification and discard an anchor required by parallel tool outputs. This extracts the request compatibility concern from #1953 and addresses #2269 independently of quarantine generation changes.

## What Changes

- Preserve raw input provenance through validation and authenticated owner forwarding.
- Keep output-only tool continuations anchored.
- Bind mixed-version capability checks to owner process identity and preserve existing safe local recovery.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: input-shape classification and owner-forward compatibility.

## Impact

Request normalization, HTTP bridge selection and owner forwarding, bridge ring metadata, and regressions. No new settings or schema. Quarantine lifetime is unchanged.

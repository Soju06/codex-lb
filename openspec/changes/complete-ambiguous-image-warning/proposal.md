# Complete ambiguous image warning

## Provenance
Local addition in the fix/python-image-bridge-consolidation branch: trial
PR2534 head 5071c18d suppresses replay for unacknowledged image sends but
omits the execution-risk warning the inline-image contract requires. See
allow-bounded-inline-images-on-bridge/consolidation.md for the full
provenance record.

## Why
Unacknowledged image disconnect and send errors suppress replay but omit the execution-risk warning required by the inline-image contract.

## What Changes
Append an ongoing-execution and duplicate-work warning for generic disconnect, clean 1000 and uncertain send failures of unacknowledged image requests. Preserve error codes, HTTP status, retry counts, text behavior and 1009 handling.

## Impact
HTTP bridge error messages and offline route regression coverage only. No deployment or configuration changes.

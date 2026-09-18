# Preserve HTTP bridge quarantine evidence lifetime

## Why
Detached or delayed completions can clear newer same-key evidence because entry generations recycle and cleanup observes state after awaits. An incident can also grow the poison registry beyond its cap.

## What Changes
Use service-lifetime generations, weak/canonical session identity, immutable pre-await cleanup fences, distinct poison/raw provenance, and deterministic bounded admission with conservative poison overflow. Preserve request classification and owner forwarding.

## Impact
Fixes #2268. Updates the responses-api-compat quarantine contract. No settings, migration, or account-health behavior changes.

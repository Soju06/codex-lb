# Select compatible internal host models

## Why
Issue #2134 reports gpt-5.5 withdrawal while the catalog still advertises it. Current main still pins Images and default account probes to that slug. The maintainer accepted ordered registry selection.

## What Changes
Use gpt-5.6-luna then gpt-5.5, selecting the first visible unsuppressed candidate and otherwise the first candidate. Share this rule across Images and default probes. Preserve explicit probe models and public image model IDs.

## Impact
Images adapter, account probes, existing settings documentation. No settings or retries added.

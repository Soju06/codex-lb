## Why

Source models can store `base_instructions` in their metadata, but the catalog
projection leaves the first-class registry field empty. Codex clients receive
an empty prompt even when the source supplied coding instructions. This is the
catalog-projection part of issue #2499.

## What Changes

- Copy string-valued source `base_instructions` into the existing registry field.
- Preserve the empty default for missing or non-string values.
- Verify the value through both Codex catalog routes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-catalog-compat`: source-model catalog entries preserve supplied instructions.

## Impact

The change affects source-model catalog projection and its unit and route tests.
It adds no setting, dependency, migration or dashboard change. Namespace tool
inference is a separate contract proposal; this change retains existing filtering.

The reproduction and reference fix are from nhdong1993 in #2499 and fork commit
`00164ab06ccdfc868fa2e04a168a804147606325`.

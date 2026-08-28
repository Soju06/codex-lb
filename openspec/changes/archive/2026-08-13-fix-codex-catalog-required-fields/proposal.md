# Change: Complete required Codex model-catalog fields

## Why

Codex 0.144.3 deserializes every entry in the native model catalog as one
`ModelsResponse`. Hidden metadata retained from codex-lb's older bootstrap
entries can omit the non-defaulted `truncation_policy` and
`experimental_supported_tools` fields. One such hidden entry makes Codex reject
the entire otherwise-valid catalog, retry model refresh continuously, and use
fallback metadata.

## What Changes

- Make the Codex-native catalog rendering boundary complete the required wire
  fields when legacy bootstrap, retained, or persisted metadata omits them.
- Use model-appropriate conservative truncation defaults for bundled models and
  an empty experimental-tool list, while preserving wire-valid values supplied
  by a live upstream or model source and repairing malformed required-field
  shapes.
- Add route-level regressions for a partial refresh that leaves older bundled
  models as hidden metadata and for the uninitialized bootstrap catalog.

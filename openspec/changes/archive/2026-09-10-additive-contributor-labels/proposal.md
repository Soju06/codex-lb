## Why

The PR labeler classifies only migrations. API-created issues miss the bug, enhancement, and triage defaults supplied by issue forms.

## What Changes

- Add existing documentation, python, frontend, ci, and docker labels from changed PR paths.
- Classify new issue titles using the repository's bug and feat prefixes, plus fix and docs prefixes.
- Add triage to newly opened issues regardless of submission method.
- Preserve existing labels and all review, lifecycle, and approval decisions.

## Capabilities

### New Capabilities

- `contributor-labeling`: Additive PR area classification and new-issue classification.

### Modified Capabilities

None.

## Impact

GitHub metadata workflows only. The trusted default branch must adopt these workflows before upstream events use them. No application or frontend behavior changes.

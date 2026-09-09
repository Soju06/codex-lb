## Context

The strict validator identifies exactly 22 existing capability specs whose Purpose line was generated as a placeholder. The requirements and implementation already exist; the task is to make the canonical documentation self-describing without inventing new behavior.

## Decisions

1. Edit only the `## Purpose` paragraph in each listed canonical spec.
2. Derive each statement from the capability's existing requirement headings and scenarios.
3. Keep the prose concise, factual, and free of normative MUST/SHALL language; requirements remain the sole normative source.
4. Do not add rendered `docs/` pages or modify unrelated capabilities.

## Verification

Run `openspec validate --specs --strict`, compare the failed-item count with the baseline, and run `git diff --check`. No runtime test suite is required beyond repository documentation validation.

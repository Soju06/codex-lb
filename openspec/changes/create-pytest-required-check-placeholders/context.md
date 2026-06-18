# CI required check placeholders

The default-branch ruleset currently requires individual pytest matrix contexts,
not only the aggregate `CI Required` job. GitHub can leave required matrix
contexts missing when the matrix job itself is skipped with a job-level `if`.
That creates a merge-blocking expected check for PRs that intentionally skip
backend work through path filtering.

The safer workflow shape is to instantiate the matrix every time and put the
path-filter condition on the expensive steps. Non-backend PRs still emit the
same required context names, but each context exits through a single placeholder
step. Backend PRs skip the placeholder and run the real checkout/setup/test
steps.

## Context

See proposal.md. The OpenSpec job currently checks out a shallow synthetic PR merge and validates canonical specs only.

## Goals / Non-Goals

Select exactly the active folders owned by the event diff. General CI cleanup and repairs to existing invalid deltas are outside this change.

## Decisions

Use one stdlib Python command as the CI interface. Read GitHub's event file and use pinned commit IDs. For PRs, compute the merge base with the actual PR head; target-only changes must not enter the selection. Keep the normal merge checkout so validation checks the candidate CI will merge. Fetch full history so both parents and their ancestry exist.

Use a NUL-delimited Git diff with rename detection disabled. Rename sources and destinations then appear as deletion/addition paths, including moves between folders. Validate each surviving folder once, even if its proposal was deleted. Skip fully deleted folders and archive paths.

Invoke the pinned OpenSpec validator with argument arrays and explicit change type. Do not run blanket change validation. Missing history fails closed. Push and merge queue use their event ranges; a zero before SHA on a new branch selects all tracked head paths.

## Risks / Trade-offs

Full history costs more checkout time. It avoids incomplete merge-base calculations and additional authenticated fetch logic. Local regression tests use real temporary Git repositories and an external validator stand-in; a separate proof runs the pinned validator against valid and invalid deltas.

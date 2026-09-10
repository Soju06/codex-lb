## Why

Combining the receipt candidate with current main leaves separate receipt and spool-retention migration heads. The documented `codex-lb-db upgrade head` command fails before schema upgrade.

## What Changes

Add an explicit schema-neutral merge revision joining the existing receipt/request-log merge and spool-retention heads. Preserve every existing revision and parent edge.

## Impact

Upgrades from either populated parent converge on one head. Downgrading only the merge restores both parent stamps without changing their schemas or rows. Receipt lifetime, reclamation, settlement and mixed-version activation decisions remain open.

# Fork Model-Transition Owner Conflicts

## Why

A durable hard continuity lookup can still identify the old model owner's
account when the client starts a fresh request on an incompatible model. If
bridge creation reports `continuity_owner_conflict`, the current path surfaces
the 502 even when the payload is account-neutral and can safely start a new
local lane.

## What Changes

- Fork eligible local model-transition owner conflicts onto one new
  account-neutral HTTP bridge lane.
- Exclude the conflicting owner, force local creation, and keep the child lane
  hard once selected.
- Clear parent-derived request state so the child lane cannot rebind the old
  turn alias.

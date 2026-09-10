## Why

Submit interruption cleanup can lose a recovery claim after durable rollback
succeeds but its in-memory fence rollback fails. Root admission also excludes
the valid string input shorthand accepted by replay normalization.

## What Changes

- Retain claim metadata until both compensation stages succeed and remember
  durable rollback completion for the exact claim so cleanup can retry its fence.
- Treat nonempty string input and equivalent message arrays consistently.
- Exercise interruption failure/retry and real submit admission paths.

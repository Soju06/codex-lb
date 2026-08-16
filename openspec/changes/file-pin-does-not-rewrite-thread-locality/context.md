## Purpose

Stop a live file pin from rewriting current-Codex thread locality.

## Decision

The required owner bypasses the thread PROMPT_CACHE row the same way
it already bypasses the process-session soft row.

## Example

Upload pins `file_xyz` to account A. Thread `t1` is already mapped to
account B. A Responses turn that references `file_xyz` goes to A; the
`t1` row remains B. The next unpinned `t1` turn still uses B.

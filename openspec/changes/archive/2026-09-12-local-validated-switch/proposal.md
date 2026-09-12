# Validated local connection switching

## Why
Stopping the only listener before launchd bootstrap succeeded caused outages. Rollback depended on the same unavailable launchd label.

## What Changes
Add a separate loopback TCP listener with a private Unix control socket. Candidate validation runs before changing the in-memory destination. Existing connections remain pinned; rollback switches new connections back without restarting either backend. No automatic backend termination or database migration is performed.

## Impact
Local deployment scripts and tests only. Initial migration of the existing listener requires a coordinated port handover; this change cannot transfer pre-existing sockets.

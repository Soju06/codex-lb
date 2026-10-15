# Merge affinity and invite histories

## Why

Main561311ded adds dashboard user invites after the audit migration. It creates a second head beside the published affinity/identity merge.

## What Changes

Append a no-op merge joining published200000 affinity identity and040000 invites. Preserve every published migration and all existing data, including pending/consumed/revoked invite state.

## Impact

Migration convergence and bounded regression updates only. Existing invitation, authentication, permission and affinity semantics remain unchanged.

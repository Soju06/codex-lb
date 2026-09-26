## Context

Install currently writes only config.toml and auth.json. Native model discovery already applies model/source scopes and removes upstream alias mappings. Reuse the native catalog builder instead of copying its server-side serialization or maintaining a hardcoded model list.

## Goals / Non-Goals

**Goals:** Refresh the catalog at execution, preserve full model metadata, configure a safe transport for all selectable models, and keep recoverable private backups.

**Non-Goals:** Install Codex itself, synchronize catalogs in the background, change account/source routing, or distribute upstream tokens.

## Decisions

- Fetch `/api/key-dashboard/models` using the exported codex-lb key at each script execution. Keep only list-visible API models; preserve every field on retained entries. This dedicated self-service endpoint always validates the key: the ordinary proxy models route deliberately ignores credentials when global auth is disabled. It uses the existing native catalog builder without creating inference reservations. The server remains authoritative for source assignment, aliases, and authorization.
- Use Python 3 standard library on Unix for HTTP, JSON, path encoding and file preparation. Windows uses PowerShell's JSON/HTTP support. No pip/npm packages are needed. Reject redirects, bound downloads with timeouts, and validate before replacing files.
- Set `model_catalog_json` to the absolute catalog path, escaped as TOML. Validate the exported enforced/first allowed model against the fetched catalog; unrestricted keys select the first visible catalog model. If the exported model is no longer available, stop and require a newly exported script rather than installing an unusable configuration.
- Enable provider WebSockets only when every installed entry advertises `prefer_websockets=true`. This conservatively uses HTTP for a mixed catalog so switching models works with one provider.
- Back up config, auth and `codex-lb-models.json` in the same private directory, stage all three new files and then replace. Keep existing symlink checks and credential protection.

## Risks / Trade-offs

- Python 3 may be absent on Unix clients -> explicit prerequisite error before mutation; state the requirement in setup guidance.
- Catalog/network/key errors -> fail before file replacement, without dumping response bodies or credentials.
- Concurrent model/source policy changes -> each run refreshes eligibility; a removed exported default requires re-exporting the installer.
- PowerShell JSON depth defaults lose metadata -> explicitly use a sufficiently deep serializer.
- Replacement is sequential, not a multi-file atomic transaction -> stage first and retain all previous files for recovery on a later filesystem failure.

## Migration Plan

Existing clients opt in by rerunning Install after deployment. Restart clients to load the local catalog; rerun after alias/capability changes. Restore all three backed-up files to undo local configuration. Deployment is a separate operator action.

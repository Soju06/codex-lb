## 1. Schema & Backend Persistence

- [x] 1.1 Add `http_responses_session_bridge_subagent_prompt_cache_ttl_seconds` column to `dashboard_settings` table (nullable Integer; `NULL` means No Cache), with Alembic migration revision
- [x] 1.2 Add the field to `DashboardSettings` ORM model in `app/db/models.py`
- [x] 1.3 Add the field to `DashboardSettingsData` dataclass in `app/modules/settings/service.py`
- [x] 1.4 Add the field to `DashboardSettingsUpdateData` for persistence through the settings API
- [x] 1.5 Add the field to `DashboardSettingsResponse` and `DashboardSettingsUpdateRequest` Pydantic schemas in `app/modules/settings/schemas.py`

## 2. Subagent Detection & Affinity

- [x] 2.1 Detect subagent sessions in `_get_or_create_http_bridge_session` from `x-parent-session-id`.
- [x] 2.2 Treat `NULL` as No Cache and suppress PROMPT_CACHE sticky lookup and persistence for subagents.
- [x] 2.3 Use the configured positive value as the subagent PROMPT_CACHE mapping TTL.
- [x] 2.4 Close subagent bridge sessions immediately after their response stream ends.
- [x] 2.5 Delete stale No Cache subagent mappings without deleting retained mappings or the canonical parent mapping.

## 3. Settings API Wiring

- [x] 3.1 Wire the new field through `SettingsService.get_settings()` and `update_settings()` in `app/modules/settings/service.py` so the dashboard cache propagates the value
- [x] 3.2 In `_http_bridge_runtime_config` (`helpers.py`), expose the nullable subagent affinity TTL
- [x] 3.3 Pass the subagent TTL through session creation and account selection

## 4. Frontend: Subagent Affinity Setting

- [x] 4.1 Add `httpResponsesSessionBridgeSubagentPromptCacheTtlSeconds` to the frontend `DashboardSettings` type in `frontend/src/features/settings/schemas.ts`
- [x] 4.2 Add field to the `RoutingSettingsDraft` type, the `createRoutingSettingsDraft()` factory, and the `buildSettingsUpdateRequest` helper
- [x] 4.3 Add an optional integer input labelled "Subagent prompt-cache affinity TTL" (seconds); empty means No Cache

## 5. Spec/Context Updates

- [x] 5.1 Update `openspec/specs/sticky-session-operations/context.md` with the subagent mapping and bridge lifecycle decisions

## 6. Tests

- [x] 6.1 Add backend test verifying that No Cache subagents do not read or write PROMPT_CACHE mappings
- [x] 6.2 Add backend test verifying that a positive subagent TTL is used for PROMPT_CACHE mapping selection
- [x] 6.3 Add backend test verifying that `NULL` and positive TTL values are persisted and returned correctly through the settings API
- [x] 6.4 Add frontend test for the optional subagent TTL Routing Settings control (validation, save, error state)
- [x] 6.5 Add backend regression coverage that completed No Cache subagent sessions delete stale mappings without deleting retained or parent mappings
- [x] 6.6 Add backend regression coverage that completed `internal_unanchored_parallel` forks close immediately and release their stream leases
- [x] 6.7 Add `is_subagent` field to sticky sessions API response and dashboard display
- [x] 6.8 Cap cleanup scheduler interval at 30 seconds for prompt subagent TTL cleanup

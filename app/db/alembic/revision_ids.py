from __future__ import annotations

import re

REVISION_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[a-z0-9_]+$")

OLD_TO_NEW_REVISION_MAP: dict[str, str] = {
    "000_base_schema": "20260213_000000_base_schema",
    "001_normalize_account_plan_types": "20260213_000100_normalize_account_plan_types",
    "002_add_request_logs_reasoning_effort": "20260213_000200_add_request_logs_reasoning_effort",
    "003_add_accounts_reset_at": "20260213_000300_add_accounts_reset_at",
    "004_add_accounts_chatgpt_account_id": "20260213_000400_add_accounts_chatgpt_account_id",
    "005_add_dashboard_settings": "20260213_000500_add_dashboard_settings",
    "006_add_dashboard_settings_totp": "20260213_000600_add_dashboard_settings_totp",
    "007_add_dashboard_settings_password": "20260213_000700_add_dashboard_settings_password",
    "008_add_api_keys": "20260213_000800_add_api_keys",
    "009_add_api_key_limits": "20260214_000000_add_api_key_limits",
    "010_add_idx_logs_requested_at": "20260215_000000_add_idx_logs_requested_at",
    "011_add_api_key_usage_reservations": "20260218_000000_add_api_key_usage_reservations",
    "012_add_import_without_overwrite_and_drop_accounts_email_unique": (
        "20260218_000100_add_import_without_overwrite_and_drop_accounts_email_unique"
    ),
    "013_add_dashboard_settings_routing_strategy": "20260225_000000_add_dashboard_settings_routing_strategy",
}

NEW_TO_OLD_REVISION_MAP: dict[str, str] = {new: old for old, new in OLD_TO_NEW_REVISION_MAP.items()}

LEGACY_MIGRATION_TO_NEW_REVISION: dict[str, str] = {
    "001_normalize_account_plan_types": OLD_TO_NEW_REVISION_MAP["001_normalize_account_plan_types"],
    "002_add_request_logs_reasoning_effort": OLD_TO_NEW_REVISION_MAP["002_add_request_logs_reasoning_effort"],
    "003_add_accounts_reset_at": OLD_TO_NEW_REVISION_MAP["003_add_accounts_reset_at"],
    "004_add_accounts_chatgpt_account_id": OLD_TO_NEW_REVISION_MAP["004_add_accounts_chatgpt_account_id"],
    "005_add_dashboard_settings": OLD_TO_NEW_REVISION_MAP["005_add_dashboard_settings"],
    "006_add_dashboard_settings_totp": OLD_TO_NEW_REVISION_MAP["006_add_dashboard_settings_totp"],
    "007_add_dashboard_settings_password": OLD_TO_NEW_REVISION_MAP["007_add_dashboard_settings_password"],
    "008_add_api_keys": OLD_TO_NEW_REVISION_MAP["008_add_api_keys"],
    "009_add_api_key_limits": OLD_TO_NEW_REVISION_MAP["009_add_api_key_limits"],
    "010_add_idx_logs_requested_at": OLD_TO_NEW_REVISION_MAP["010_add_idx_logs_requested_at"],
}

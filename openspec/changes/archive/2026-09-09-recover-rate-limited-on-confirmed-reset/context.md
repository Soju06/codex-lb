## Warm-up settings lifecycle

`limit_warmup_exhausted_threshold_percent` no longer controls reset-confirmed warm-up. A real selected-window reset must be eligible regardless of prior usage, so retaining that threshold in candidate selection would contradict this change's behavioral contract.

This PR intentionally leaves the persisted setting, settings API, and dashboard field in place to keep the recovery fix to one concern. Their end-to-end removal will be handled as a dedicated OpenSpec follow-up under the settings-surface reduction tracked by #1340, including the database model and migration, API schemas, dashboard control, tests, and operator documentation.

`limit_warmup_cooldown_seconds` remains active but intentionally applies only to staggered idle warm-up. Reset-confirmed warm-up uses the atomic account/window/reset claim instead: an attempt for the same tuple is deduplicated, while a distinct real reset is not suppressed merely because another attempt happened recently.

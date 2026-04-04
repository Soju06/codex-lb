## Why
`uvx codex-lb` currently fails during startup on Windows because `app.core.resilience.memory_monitor` imports the Unix-only standard-library `resource` module at import time. That crash prevents the application from starting before any memory thresholds are evaluated.

## What Changes
- Make the resilience memory monitor load platform-specific RSS providers conditionally instead of importing `resource` unconditionally.
- Add a Windows RSS lookup path that does not require extra dependencies.
- Ensure unsupported RSS providers degrade to a no-crash fallback so startup and request handling continue.
- Add regression tests that cover Windows import behavior and unavailable provider fallback semantics.

## Impact
- Windows users can start the application without `ModuleNotFoundError: No module named 'resource'`.
- Existing Linux and macOS RSS behavior remains intact.
- When no RSS provider is available, memory-pressure rejection is effectively disabled instead of crashing the service.

# runtime-portability Specification

## Purpose

Define runtime portability contracts so resilience features degrade safely across supported operating systems.

## Requirements

### Requirement: Memory monitor startup remains portable across supported platforms

The resilience memory monitor MUST NOT prevent application startup on platforms where Unix-specific standard-library modules are unavailable. The system MUST resolve RSS measurement through a platform-appropriate provider when one exists, and MUST fall back to treating memory pressure telemetry as unavailable instead of crashing when no provider is available.

#### Scenario: Windows startup does not import Unix-only resource module

- **WHEN** the application starts on Windows
- **AND** the Python runtime does not provide the Unix-only `resource` module
- **THEN** the memory monitor imports successfully
- **AND** application startup continues without `ModuleNotFoundError`

#### Scenario: RSS provider unavailable does not crash request handling

- **WHEN** the memory monitor cannot resolve RSS from `psutil`, a platform API, or `resource`
- **THEN** RSS lookup returns an unavailable result without raising to callers
- **AND** memory warning and rejection checks do not crash request handling

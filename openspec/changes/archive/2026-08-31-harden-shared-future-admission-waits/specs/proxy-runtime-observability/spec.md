## ADDED Requirements

### Requirement: Event-loop scheduling lag is observable

The system MUST sample event-loop scheduling lag (timer drift of a
once-per-second sleep) while serving and export it as the
`codex_lb_event_loop_lag_seconds` gauge. Samples at or above the configured
warning threshold MUST increment `codex_lb_event_loop_lag_warnings_total` and
emit a warning log that names the observed lag, the worst lag suppressed since
the previous line, and the threshold; the warning log MUST be rate-limited so
a sustained stall cannot flood the log. The threshold MUST be configurable via
`event_loop_lag_warn_threshold_seconds` with a working default requiring no
operator action, and `0` MUST disable the watchdog.

#### Scenario: Starved event loop produces an explicit operator signal

- **WHEN** the event loop is starved (callback storm, synchronous work on the
  loop, or CPU saturation) and scheduling lag reaches the warning threshold
- **THEN** `codex_lb_event_loop_lag_warnings_total` increments
- **AND** a rate-limited `event_loop_lag` warning names the observed lag and
  threshold, distinguishing loop starvation from upstream slowness

#### Scenario: Healthy loop stays quiet

- **WHEN** scheduling lag stays below the warning threshold
- **THEN** the gauge is still updated for dashboards
- **AND** no warning is logged and the warning counter does not increment

#### Scenario: Watchdog can be disabled

- **WHEN** `event_loop_lag_warn_threshold_seconds` is set to `0`
- **THEN** the watchdog task is not started

## ADDED Requirements

### Requirement: Helm preStop shares the application drain deadline

The Helm lifecycle hook MUST start local drain and poll its strict status. The configured routing dwell and application deadline MUST be measured from Python preStop-helper start. The hook MUST convey its helper-anchored absolute monotonic drain deadline to the loopback drain-start endpoint; that deadline-bearing request MUST commit the one-way process barrier. The application MUST reject non-finite values, clamp the supplied deadline so it cannot exceed the configured application timeout measured from receipt, and return the effective committed absolute deadline. The hook MUST validate that response and use the earlier of its local and returned deadlines. Local drain-start request latency or an earlier process deadline MUST therefore consume that single absolute budget rather than create another period. The hook MUST exit once the dwell has elapsed with `draining=true` and `in_flight=0`, or when the effective application drain deadline is exhausted. It MUST NOT add a second fixed drain period. A start, status, or status-schema failure MUST end the hook promptly so kubelet can deliver SIGTERM as the fallback, without rolling back a barrier already accepted by the application. Kubernetes termination grace MUST be documented as beginning before helper launch, with exec/Python launch latency consuming the hard grace but not restarting or shortening the helper-anchored application budget.

#### Scenario: Routing dwell completes with no in-flight work

- **WHEN** the Python preStop helper starts the routing dwell and status reports zero in-flight work
- **THEN** the hook waits through the routing dwell measured from helper start
- **AND** the loopback drain-start request establishes the helper-start-anchored application deadline
- **AND** local drain-start request latency does not restart that dwell
- **AND** exits without waiting through the rest of the drain timeout

#### Scenario: Drain-start request cannot extend the deadline

- **WHEN** the loopback drain-start request reaches the application after helper start
- **THEN** the application uses no deadline later than the hook's supplied absolute deadline
- **AND** clamps that value to no later than its configured timeout from receipt
- **AND** commits the process barrier and returns the effective deadline
- **AND** the hook bounds all later polling by that returned deadline
- **AND** rejects a non-finite supplied deadline

#### Scenario: Work remains after routing dwell

- **WHEN** routing dwell has elapsed and status still reports positive `in_flight`
- **THEN** the hook continues polling until `in_flight=0` or the shared deadline

#### Scenario: Drain start or status fails

- **WHEN** the local drain start request, status request, or status schema fails
- **THEN** preStop exits promptly with failure
- **AND** it does not blindly sleep through another timeout

#### Scenario: Helm timing values are unsafe

- **WHEN** `config.shutdownDrainTimeoutSeconds` is shorter than `preStopSleepSeconds`
- **OR** `terminationGracePeriodSeconds` is shorter than `config.shutdownDrainTimeoutSeconds + 32`
- **THEN** chart rendering fails with a helpful timing-contract error

#### Scenario: Operator reads shutdown documentation

- **WHEN** an operator inspects Helm shutdown tuning
- **THEN** documentation states that preStop and SIGTERM share one application deadline
- **AND** distinguishes the earlier Kubernetes hard-grace start from the Python helper's application-deadline start
- **AND** uses the nested `config.shutdownDrainTimeoutSeconds` values key
- **AND** warns that an old or custom `terminationGracePeriodSeconds` from a values file, `--set`, or `--reuse-values` below `config.shutdownDrainTimeoutSeconds + 32` makes Helm rendering fail before resources are applied
- **AND** states that the minimum is the configured drain timeout plus 32 seconds, is 62 seconds at the default 30-second drain timeout, and that the chart default is 65 seconds
- **AND** directs the operator to remove the override or raise it to at least the computed minimum before installing or upgrading
- **AND** states that omitting the key under `--reuse-values` retains the stored low value, so that path must set at least the computed minimum explicitly, while adopting the chart default requires an intentional non-reuse or `--reset-values` upgrade with the key absent

### Requirement: Shipped launch paths use the pre-connection drain server

Every shipped or documented launch path for the main application MUST delegate to the project CLI so direct SIGTERM commits the application drain barrier before Uvicorn closes connections. Development Compose MUST preserve source-watch behavior without replacing the project server with Uvicorn's reload supervisor.

#### Scenario: Development Compose watches application source

- **WHEN** the development Compose service is started with watch enabled
- **THEN** it launches the main application through `python -m app.cli`
- **AND** an application source sync restarts that service
- **AND** it does not launch direct Uvicorn reload

#### Scenario: Operator follows a shipped local command

- **WHEN** an operator follows a repository-documented command for the main application
- **THEN** that command delegates to `app.cli`
- **AND** direct SIGTERM reaches the pre-connection drain server

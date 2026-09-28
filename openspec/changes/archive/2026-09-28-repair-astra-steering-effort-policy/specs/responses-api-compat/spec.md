## ADDED Requirements

### Requirement: Astra steering retains original effective reasoning policy
An Astra steering continuation SHALL validate the parent's effective
pre-wire reasoning effort against its refreshed API-key policy. If the
parent had no effort and the key did not enforce one, the continuation
SHALL NOT invent an effort. A client `ultra` effort SHALL remain `ultra`
for policy evaluation even when the upstream create used its `max` alias.

#### Scenario: Missing effort survives a restrictive key
- **GIVEN** an Astra create with no reasoning effort under a key allowing only `high`
- **WHEN** that response is steered under unchanged key policy
- **THEN** the steer SHALL be admitted without an invented `medium` effort

#### Scenario: Ultra remains a client-plane policy value
- **GIVEN** an Astra create with client or enforced effort `ultra` accepted by its key
- **WHEN** that response is steered under unchanged key policy
- **THEN** the steer SHALL be admitted as `ultra` while the upstream create uses `max`

#### Scenario: Refreshed forbidden effort remains forbidden
- **GIVEN** an Astra create with an explicit effort
- **WHEN** refreshed key policy excludes that effort at steering admission
- **THEN** the steer SHALL fail without an upstream steering frame

### Requirement: Steering admission uses the documented byte bound
Queued steering input SHALL be bounded by its existing per-frame and
per-continuation byte budgets, without a separate queued-submission count
limit. An unsupported aiohttp writer transport SHALL reject only the
steering-sensitive explicit send before dispatch with status `503` and
code `steering_not_supported`; ordinary sends SHALL remain usable.

#### Scenario: Count alone does not reject a bounded queue
- **GIVEN** queued steering inputs remain within the byte budget
- **WHEN** another steer is admitted after 32 submissions
- **THEN** its count alone SHALL NOT reject it

#### Scenario: Missing aiohttp instrumentation fails closed
- **GIVEN** a writer without the required transport seam
- **WHEN** an explicit steering continuation requires dispatch observation
- **THEN** its send SHALL fail with `503` and `steering_not_supported` before dispatch

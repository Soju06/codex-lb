## Context

#2089 bundled steering with async tools and configuration-update policy.
Maintainer required a split. rust-v0.153.4 does not emit `response.steer`.

## Goals / Non-Goals

**Goals:** owned-socket steering, one successor reservation for queued
steers, prepare-before-swap for explicit continuations.

**Non-Goals:** Ultra/`configuration_update` (#2097), async tool
continuity (#2099), catalog (#2085).

## Decisions

FOR UPDATE only on extend/reduce (`get_usage_reservation_for_update`),
then limit rows. Finalize/release stay on the existing unlocked read.

Do not globally fingerprint `upstream_payload["input"]`.

Release a steering placeholder only after
`_prepare_websocket_response_create_request` succeeds.

Preparation, owner resolution and admission can yield to the upstream reader.
Revalidate the exact control-map continuation and its pending, unassigned
placeholder under `pending_lock` before registering the replacement. Perform
the placeholder removal and continuation handoff in that same critical section.
If ownership changed, reject the explicit create with `response_not_found` via
the existing reservation/admission cleanup; do not revive a rejected steer or
replace a successor that already owns a response ID. For example, a final
`response.steer.failed` during admission must not leave an explicit create with
a steering parent but no matching control-map entry.

Rejected: landing the full #2089 branch.

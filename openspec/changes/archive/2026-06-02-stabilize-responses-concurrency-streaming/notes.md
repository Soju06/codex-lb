# Baseline Evidence

- `/v1/responses` route wiring lives in `app/modules/proxy/api.py` and prefers the HTTP responses bridge for eligible Responses traffic.
- HTTP responses bridge session/capacity/queue behavior lives in `app/modules/proxy/service.py`, including local queue-full and capacity-exhausted rejection paths.
- Process-wide admission is controlled by `app/core/resilience/bulkhead.py` and `app/modules/proxy/work_admission.py`.
- Account selection and runtime health live in `app/modules/proxy/load_balancer.py`; pre-change routing uses persisted usage/runtime health but lacks an in-flight lease that is atomically acquired with selection.
- Upstream HTTP error classification starts in `app/core/clients/proxy.py` and helper/balancer classification paths.
- SSE keepalive helpers live in `app/core/utils/sse.py`, with streaming response header/event handling in `app/modules/proxy/api.py`.
- Phase 1 topology guarantee is per proxy instance and service-wide only for single replica or multi-replica deployments with sticky ingress/deterministic owner routing.

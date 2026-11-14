# Verification

- RED: 100-second boundary assertion failed against the original cap (`160.0 != 200.0`).
- GREEN: focused watchdog/eventless/lifecycle suite: 47 passed. Ruff and git diff --check passed.
- Broad bridge suite: 1016 passed, 54 failed. Original-cap comparison, excluding the changed boundary test: 1015 passed, the same 54 failures. Existing image-support patches leave stale fixture fields/signatures (inline_images_enabled, response_create_max_bytes); no additional failure identities.
- OpenSpec change strict validation passed. Canonical specs ordinary validation: 67/67 valid. Repository-wide strict validation fails existing long-requirement warnings (8 pass / 59 fail); this change remains unarchived pending that gate.
- Local activation: verified no in-flight or restart-blocking requests, drained, restarted existing scheduled task codex-lb (local test), PID 19100 -> 19360. Production interpreter imports the intended helpers module with cap 100.0. /health/ready is OK with database OK and ring membership true. Subsequent real DSH bridge request 6831 succeeded (45101 ms). No controlled live 60-to-100-second upstream stall was induced.
- No image-window code/config changes; its step-based expiration is intended. KeepSteps 2/4/8 were evaluated using fixed recorded trajectories, not an online behavior experiment.

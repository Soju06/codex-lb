## 1. Spec and Reproduction

- [ ] 1.1 Add the live direct WebSocket continuity contract requirements.
- [ ] 1.2 Add a focused regression for the Codex Desktop `Continue` raw-error shape.
- [ ] 1.3 Add a live/parity verifier that reports deployed build identity and recent raw client errors.

## 2. Repair Path

- [ ] 2.1 If the focused regression fails on current repo code, harden the direct WebSocket public error boundary.
- [ ] 2.2 If current repo code passes but live canary fails, rebuild/redeploy local `codex-lb` from the current commit instead of changing service logic.
- [ ] 2.3 Make request-log persistence failures visible in health or verifier output.

## 3. Watchdog Recovery

- [ ] 3.1 Update the local watchdog to use Colima's Docker context/socket explicitly.
- [ ] 3.2 Verify watchdog restart behavior against the live `codex-lb` container without pushing remote work.

## 4. Verification

- [ ] 4.1 Run targeted continuity pytest suites.
- [ ] 4.2 Run `openspec validate --specs`.
- [ ] 4.3 Run the live verifier before and after local redeploy.
- [ ] 4.4 Record the proof boundary in the final handoff.

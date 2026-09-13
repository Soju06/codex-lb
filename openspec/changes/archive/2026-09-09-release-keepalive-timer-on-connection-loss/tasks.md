## 1. Implementation

- [x] 1.1 Override `connection_lost` in `UpgradeTolerantHttpToolsProtocol` to
      call the stock teardown and then cancel the keep-alive timer
      unconditionally (no transport close on the error path; the loop already
      force-closed it).
- [x] 1.2 Add the identical override to `UpgradeTolerantH11Protocol` for the
      httptools-less fallback.
- [x] 1.3 Record the second reason the subclasses exist in both module
      docstrings and in the h2c canary's retirement note, so an upstream fix
      for the upgrade-offer defect does not retire the keep-alive fix with it.
- [x] 1.4 Lower the `--timeout-keep-alive` / `UVICORN_TIMEOUT_KEEP_ALIVE`
      default from 7200 s to 300 s and rewrite the help text to state the
      invariant (server idle window above the client pool idle timeout).
- [x] 1.5 Document the knob and its invariant on the reverse-proxy docs page.

## 2. Validation

- [x] 2.1 Fake-transport regressions over both subclasses: after a completed
      request and `connection_lost(ConnectionResetError)`, the timer is
      cancelled and the protocol is garbage-collectable; clean close still
      closes the transport and clears the timer; the timer still closes an
      idle connection; an error close mid-request leaves the cycle
      disconnected and arms no timer.
- [x] 2.2 Live-server regression over real sockets with the production
      protocol wiring: clients complete a request and close with an RST; every
      protocol observes `ConnectionResetError` and is released.
- [x] 2.3 Canaries on stock `HttpToolsProtocol` and `H11Protocol` pinning the
      upstream defect (timer still armed, protocol still pinned after an error
      close).
- [x] 2.4 Update the pinned CLI default; keep the custom-value and invalid-env
      CLI tests.
- [x] 2.5 Re-run the real-socket reproduction (300 connections, asyncio and
      uvloop): RST close goes from 300/300 retained to 0/300.
- [x] 2.6 Run focused tests, lint, type checks, architecture check, and strict
      OpenSpec validation.

## 1. Contract and implementation

- [x] 1.1 Define negotiated optional SSE framing and shared fixtures.
- [x] 1.2 Implement bounded Rust framing, typed size failure, and body-read idle timeout.
- [x] 1.3 Switch direct native streaming Responses to framed events and preserve cleanup.

## 2. Verification and documentation

- [x] 2.1 Verify Python/Rust parity across split delimiters, UTF-8, whitespace, EOF, and size limits.
- [x] 2.2 Exercise real worker/proxy streaming, partial activity, timeout, errors, cancellation, and concurrent request isolation.
- [x] 2.3 Run Rust checks, focused Python regression tests, typing/lint, architecture and strict OpenSpec validation.
- [x] 2.4 Document ownership and verification, sync specifications, and archive only after verification.

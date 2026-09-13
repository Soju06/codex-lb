## Implementation

- [x] Negotiate native HTTP Responses completion and mark the final terminal fragment.
- [x] Stop Rust body reads at the recognized terminal and retire the Python adapter without cancellation.
- [x] Verify direct/routed SDK/native parity, fragmented terminals, late errors, cancellation, and peer isolation with the real helper.
- [x] Run Rust checks, Python regression suites, and strict OpenSpec validation; synchronize ownership documentation.

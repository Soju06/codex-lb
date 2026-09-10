## 1. Routing consistency

- [x] 1.1 Refresh usage-cap routing state after successful usage endpoint writes and verify writer/no-write unit cases.
- [x] 1.2 Run trusted-capability WebSocket rerouting before the reused-account cap gate and verify a capped ordinary socket switches to an uncapped authorized account.

## 2. Dashboard correctness and accessibility

- [x] 2.1 Apply weekly pace caps only to 10,080-minute windows and verify a non-weekly secondary duration keeps full capacity.
- [x] 2.2 Add diagonal hatching to the shared reserved-cap marker and verify all quota surfaces retain the accessible cap label and patterned style.

## 3. Validation

- [ ] 3.1 Run focused backend/frontend tests, lint/type checks, strict OpenSpec validation, migration checks, and `nix run .` startup.

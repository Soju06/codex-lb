# Change: Attest ASN and TLS randomization parity

## Why

The traffic parity report can prove source-address equality at a controlled
origin and compare a stable TLS capability profile, but it cannot independently
attest the source ASN or decide whether different ClientHello extension orders
are normal direct-Codex randomization. Treating one JA3 value as canonical
would create a fixed load-balancer fingerprint, while claiming ASN equality
without a local database and provenance would manufacture evidence.

## What Changes

- Optionally enrich controlled capture records from an operator-supplied,
  offline ASN MMDB without retaining raw source addresses or organization
  names.
- Record the ASN database digest and build metadata so A/C evidence is compared
  only under the same database provenance.
- Accept a second direct-Codex capture as the natural-randomization reference
  and compare HTTP JSON, HTTP SSE, and WebSocket ClientHello samples
  independently.
- Keep stable TLS capability fields exact while evaluating extension order with
  pairwise precedence distributions, order entropy, and a deterministic 95%
  direct-baseline bootstrap limit.
- Report insufficient samples and missing/incompatible ASN evidence as
  unobserved rather than pass.

## Impact

- Affected spec: `compatibility-tooling`
- Affected code: traffic capture addon, comparison CLI, Markdown report, tests,
  and operator documentation

## 1. Capture and parsing

- [x] 1.1 Capture Codex Responses HTTP JSON, HTTP SSE, and WebSocket traffic
      with explicit transport and flow metadata
- [x] 1.2 Redact credential headers and support metadata/full/none body modes
- [x] 1.3 Parse SSE framing and reconstruct common turns from HTTP pairs and
      WebSocket `response.create` lifecycles

## 2. Comparison and report

- [x] 2.1 Compare Path B and C as the same-run fidelity oracle while treating
      optional Path A as a structural direct baseline
- [x] 2.2 Report transport, request, event lifecycle, terminal, usage, tool, and
      missing-turn differences in JSON
- [x] 2.3 Generate a Markdown investigation report and expose a strict nonzero
      exit status for hard B/C mismatches

## 3. Documentation and validation

- [x] 3.1 Document the three-path HTTP/SSE/WebSocket workflows, safe capture
      defaults, WebSocket proxying, and generated artifacts
- [x] 3.2 Add focused parser, turn reconstruction, sanitization, comparison,
      and CLI/report tests
- [x] 3.3 Run focused pytest, Ruff, and strict OpenSpec validation

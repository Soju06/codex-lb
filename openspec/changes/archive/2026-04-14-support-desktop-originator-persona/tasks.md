## 1. Native Codex originators

- [x] 1.1 Expand native Codex originator detection to recognize `codex_atlas` and `codex_chatgpt_desktop`
- [x] 1.2 Add regression coverage proving those originators trigger native Codex transport detection

## 2. OAuth persona selection

- [x] 2.1 Add a configurable OAuth authorize originator with `codex_chatgpt_desktop` as the default
- [x] 2.2 Add regression coverage proving the configured OAuth originator is forwarded into the authorize URL

## 3. Verification

- [x] 3.1 Run targeted backend tests and diagnostics for proxy/originator changes
- [x] 3.2 Validate OpenSpec specs after the delta is added

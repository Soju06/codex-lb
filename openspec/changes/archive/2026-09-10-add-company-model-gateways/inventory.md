# Company model entrypoint inventory (2026-09-10)

The deployment excludes every GLM entry. A model is enabled only when the exact
model ID completed a synthetic tool-call/result loop through codex-lb. Dynamic
aliases and agent products are kept out of the model catalog.

| Entrypoint | Protocol exposed to Codex | Enabled models | Verification |
| --- | --- | --- | --- |
| TRAE CN raw chat | Responses adapter over TRAE SSE | 27 current non-GLM catalog variants | All passed function-call/result continuation; GPT-6 Astra also passed a real Codex CLI shell-tool task |
| Codebase LLMProxy native Responses | Responses | `codebase/gpt-5.4`, `codebase/gpt-5.2` | Both passed function-call/result continuation |
| Codebase LLMProxy native Chat | Responses-to-Chat adapter | `codebase/seed-code-preview`, `codebase/doubao-seed-2.0-code`, `codebase/doubao-seed-1.8`, `codebase/deepseek-v3.1`, `codebase/kimi-k2.6`, `codebase/kimi-k2.5`, `codebase/qwen3.6-plus`, `codebase/qwen3.5-plus` | All eight passed function-call/result continuation; Kimi K2.6 also passed a real Codex CLI shell-tool task |
| LLMBox | Native Responses | `deepseek-v4-flash-0731` | Passed function-call/result continuation and a real Codex CLI shell-tool task |

TRAE currently contributes these 27 model rows:

`Seed-Evolving`, `Seed-2.1-Pro`, `Seed-2.1-Turbo`, `openrouter-3o`,
`openrouter-3o-max`, `openrouter-2o`, `openrouter-2o-max`, `GPT-6-Astra`,
`GPT-6-Astra-max`, `GPT-5.6-Sol`, `GPT-5.6-Sol-max`, `GPT-5.6-Terra`,
`GPT-5.6-Terra-max`, `GPT-5.6-Luna`, `GPT-5.6-Luna-max`, `GPT-5.5`,
`GPT-5.5-max`, `GPT-5.4`, `DeepSeek-V4-Pro`, `DeepSeek-V4-Flash`,
`Seed-Dogfooding-2.0`, `Seed-Code`, `openrouter-1o`, `openrouter-1`,
`GPT-5.2`, `Gemini-3.1-Pro-Preview`, and `Gemini-3-Flash-Preview`.

The following entries remain excluded:

- All GLM IDs in LLMBox, TTADK and Codebase catalogs, per the deployment policy.
- LLMBox `auto` and `auto-max`, because their backing model is dynamic and cannot
  guarantee the GLM exclusion.
- Codebase `gpt-5.2-codex`, because the first tool call succeeds but the upstream
  rejects its own encrypted reasoning item on continuation.
- Native Codebase `openrouter-2o`, `openrouter-1o`, and `openrouter-1`, which emit
  upstream errors, and `deepseek-v3.2`, which returns HTTP 404. Equivalent TRAE
  raw-chat entries are verified and remain available there.
- Coco-only catalog labels `MiniMax-M2.7` and `MiniMax-M2.5`, which return HTTP
  404 when used as native model IDs, and `Test-O-New` / `Test-New`, for which no
  supported raw inference mapping is published. Coco's Gemini labels are already
  covered by the verified TRAE endpoint.
- TTADK, which currently exposes LLMBox routing with an additional GLM-only
  catalog rather than a unique eligible model.
- TMates, whose current OpenAPI surface manages agents, runs, projects, spaces
  and memory. It has no verified raw model inference endpoint, so wrapping an
  agent run as an OpenAI model would misrepresent the protocol.

Cursor-specific client models are outside this inventory. The optional scope
question about Cursor received no answer, so the inventory follows the stated
TRAE, LLMBox, TTADK, Coco and TMates scope.

Quota values remain `unknown` for all three sources. The dashboard separately
shows credential-cache presence and retained 24-hour request/token observations;
neither value is presented as upstream remaining quota.

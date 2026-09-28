# Design

The boolean is a key preference for automatic activation, not an authorization gate. False preserves upstream defaults, including future upstream changes; clients with explicit local preferences retain their own precedence. The endpoint continues enforcing existing key/account permissions.

Only the exact native `gpt-6-astra` entry is eligible. Do not add a missing model, expose a hidden model, modify a source-provider model, invent incomplete model-owned prompts, or mutate the shared registry. Merge the three activation values into complete existing upstream metadata. Malformed or missing model-message/token-budget metadata leaves that entry unchanged.

The catalog is built for each authenticated key; apply the overlay after model selection. Existing API-key update invalidation refreshes the preference. Return personalized native catalogs with private/no-cache response headers. Codex still keeps a local catalog cache: users refresh/reopen and start a new task to apply changes. Running-session extension changes are outside this feature.

No global switch or model-rule editor is needed. The existing key editor is the control surface. Key-management permissions remain unchanged. Keys shared by several people share this preference. Opting in does not grant an upstream account access to an experimental feature it lacks.

UI copy: label “Experimental Astra notes”; description “Automatically enables experimental notes for new GPT-6 Astra conversations.”


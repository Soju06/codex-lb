## Why

Some OpenAI workspace tokens expose the same `chatgpt_account_id` for multiple
human login emails in the same workspace. When codex-lb treats that upstream
workspace identifier as a unique account identity, adding or reauthorizing one
workspace member can overwrite another member's stored tokens.

## What Changes

- Preserve separate local account slots for different emails that share the
  same upstream `chatgpt_account_id`, with or without a reported workspace id.
- Keep re-import/reauth of the same email on the same slot updating that slot.
- Display the upstream ChatGPT account id as the primary workspace/account-slot
  context because OpenAI does not reliably provide a selected workspace name.
- Add regression coverage for shared workspace account identities.

## Impact

Operators can add multiple accounts from the same OpenAI workspace without the
latest OAuth/import replacing a sibling account's email and tokens.

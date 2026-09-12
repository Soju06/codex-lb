The direct WebSocket path already retains a full resend when it injects an anchor, but its account-switch validator rejects raw response IDs and encrypted reasoning. The HTTP bridge already has the projection and completeness checks needed to make this history portable.

For `[user, reasoning, assistant, new user]`, quota recovery sends `[user, assistant, new user]` to the replacement account. The original anchored request is unchanged. Retaining the verified client prefix fingerprint lets the following turn trim the client's original history against the replacement response.

This change only prepares the existing replay path. It does not relax file or turn-state ownership, reconstruct missing history, or change selection-time owner recovery.

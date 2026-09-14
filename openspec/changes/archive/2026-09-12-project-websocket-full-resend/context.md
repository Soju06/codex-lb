The direct WebSocket path already retains a full resend when it injects an anchor, but its account-switch validator rejects raw response IDs and encrypted reasoning. The HTTP bridge already has the projection and completeness checks needed to make this history portable.

For `[user, reasoning, assistant, new user]`, account-switch preparation builds `[user, assistant, new user]` from the retained body. Same-account retries keep the original body. Only an installed projection whose input matched the complete client fingerprint preserves that fingerprint for the next turn. Size-slimmed retries refresh their fingerprint so later turns resend the missing context.

This change only prepares the existing replay path. It does not relax file or turn-state ownership, reconstruct missing history, or change selection-time owner recovery.

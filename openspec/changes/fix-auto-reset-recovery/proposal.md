# Safe automatic reset recovery

The existing scheduler trusts persisted account status before spending and retries a different credit after an uncertain consume response. The consume service generates a fresh request ID every time, and successful cooldown state exists only in memory.

Add a durable redemption journal shared by automatic and manual redemption, refresh standard usage before exhaustion spending, reconcile uncertain or applied attempts before selecting another credit, and preserve the existing expiry policy. Preserve account ownership, provider eligibility, and all subscription gates. Do not reset for additional model quotas alone.

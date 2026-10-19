# Context

The local account ID identifies a seat; different seats can share an upstream workspace ID. Routing must use the intended local ID already carried by the OAuth flow.

A targeted account with an active binding uses that pool, independent of the default-pool setting. An unbound targeted account follows ordinary dashboard routing policy, including direct access when global routing is disabled. Untargeted OAuth retains its bootstrap guard whenever active bindings exist.

For example, reauthenticating a direct seat while a second seat has a bound proxy should preserve the first seat's direct route. Reauthenticating the bound seat should use its own proxy even if no default pool is configured. Invalid active bound pools fail before network activity. Removing a binding makes the account unbound under existing policy; this change does not introduce a permanent proxy-required flag or freeze configuration for a whole flow.

Server-side OAuth routing does not configure the browser used for sign-in.

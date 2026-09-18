# Recovery boundaries

Windows errors 64 and 121 indicate a failed local transport. They do not prove
that the whole host is offline, and they do not prove whether an HTTP request was
accepted. Classification therefore activates the existing account-neutral
shared-client retirement path. It does not by itself permit replay.

Only an existing typed connector exception can mark the failure pre-dispatch.
That case may retry on the same account within the original request deadline.
A raw OSError retires the concrete failed generation for later callers and
surfaces the current failure. Error message text never grants provenance.

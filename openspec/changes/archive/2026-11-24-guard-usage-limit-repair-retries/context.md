A probe can observe owner A, wait for local admission, and then find that another request has recorded B. The write compares the observed owner and timestamp. A same-account refresh advances the timestamp even within one clock tick. Cancellation waits for the write result so rollback only runs for a confirmed write.

An operator can stop a historical repair at the scalar migration while override columns already exist. The checkpoint remains for later repair work. If a subsequent relative upgrade applies its target and exits before clearing that checkpoint, a retry uses the same target.

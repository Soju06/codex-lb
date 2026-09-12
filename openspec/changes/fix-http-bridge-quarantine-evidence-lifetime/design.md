# Design

Quarantine entries retain weak owner identity and separate poison, raw-generation and first-strike provenance. The service allocator advances across entry removal and replacement. Cleanup captures the evidence before awaits and checks identity before clearing the primary key. A distinct recovery key uses its exact captured provenance, including observed absence.

Admission preserves active poison entries and sorts weaker eviction candidates deterministically. A single overflow deadline covers rejected poison keys without another unbounded map. The existing classifier consults this poison evidence; its payload rules and forwarding contract are unchanged.

The quarantine module keeps these operations together because admission, expiry, revocation and completion cleanup mutate the same evidence record. Moving individual methods to satisfy a line count would obscure that shared invariant.

Tests cover primary and recovery generation races, weak and canonical ownership, concurrent first strikes, poison expiry, bounded overflow, native-interpreted events and public HTTP promotion. The public alias-persistence race reproduces the lost first strike on c0beaaadd and passes with this change.

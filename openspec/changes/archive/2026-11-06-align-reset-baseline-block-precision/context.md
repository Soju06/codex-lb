The block timestamp is stored as integer UTC epoch seconds. Its subsecond ordering is unknown, so this repair retains the existing conservative recovery boundary rather than accepting every fractional sample in that second. The resolver discards such samples before finding an anchor, allowing a later valid baseline to prove a real reset. If none exists, recovery remains fail-closed.

All fixtures use synthetic account data. The candidate is prepared in an isolated source snapshot; the owner checkout and production service are unchanged.

SCIM accepts at most 64 KiB per resource write. The global raw-body guard
remains in place, but the route accumulates only chunks that fit its smaller
budget. If a request sends 64 KiB, then one byte, then a tail, the route
returns SCIM 413 on the second chunk and never receives the tail. The
Content-Length precheck still refuses a declared oversize before reading.

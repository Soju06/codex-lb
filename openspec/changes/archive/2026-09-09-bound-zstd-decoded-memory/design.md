## Context

Gzip already wraps a file-like decoder with `_read_limited`, and deflate bounds
each decompressor call to the remaining decoded-byte budget plus one byte.
Zstd instead calls `ZstdDecompressor.decompress()` first. Its
`max_output_size` controls a one-shot allocation size; it is not a streaming
quota. The fallback reader runs only after that attempt raises.

Official python-zstandard guidance recommends streaming APIs when the exact
output size is unknown because one-shot decompression can require large
allocations.

## Goals / Non-Goals

**Goals:**

- Bound retained zstd output incrementally using the existing decoded limit.
- Reuse the middleware's established bounded-reader behavior.
- Preserve all current response envelopes and encoding composition.

**Non-Goals:**

- Adding a new zstd window-size setting.
- Changing raw-body limits or multipart exceptions.
- Changing support for concatenated frames or trailing data beyond current
  `stream_reader` behavior.

## Decisions

Always create a zstd `stream_reader` over the encoded bytes and pass it to
`_read_limited`. Each read is capped by the existing 64 KiB chunk size, and the
consumer checks cumulative output before extending its retained buffer.

Alternative: keep one-shot decompression with a smaller
`max_output_size`. Rejected because that argument still sizes a native
allocation rather than enforcing output incrementally.

Alternative: introduce a new zstd-specific helper or setting. Rejected because
the existing bounded-reader contract already provides the required behavior
with less code and no new operator surface.

## Risks / Trade-offs

- Streaming may add small iterator/read overhead -> request bodies are already
  bounded and correctness takes priority over one-shot throughput.
- Decoder exceptions must retain existing mapping -> the middleware continues
  to catch the same zstandard exceptions at the request boundary.

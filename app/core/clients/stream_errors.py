"""Transport-independent failures from bounded upstream SSE consumption."""


class StreamIdleTimeoutError(Exception):
    pass


class StreamEventTooLargeError(Exception):
    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(f"SSE event exceeded {limit_bytes} bytes (received {size_bytes} bytes)")
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes

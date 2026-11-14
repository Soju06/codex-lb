from app.modules.proxy._service.http_bridge.connect_diagnostics import exception_metadata


def test_redacts_messages_and_preserves_numeric_cause():
    root = ConnectionResetError(10054, "SECRET")
    outer = TimeoutError("SECRET")
    outer.__cause__ = root
    assert exception_metadata(outer) == [{"type": "TimeoutError"}, {"type": "ConnectionResetError", "errno": 10054}]


def test_bounds_unknown_types_and_cycles():
    error = type("SECRET_CLASS", (Exception,), {})("SECRET")
    error.errno = "SECRET_CODE"
    error.__cause__ = error
    assert exception_metadata(error) == [{"type": "Other"}]
    for _ in range(9):
        outer = TimeoutError("SECRET")
        outer.__cause__ = error
        error = outer
    assert len(exception_metadata(error)) == 5

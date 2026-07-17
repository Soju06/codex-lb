from fastapi.routing import APIRoute

from app.modules.proxy.api import codex_preflight_router, router


def test_codex_alpha_search_route_allows_browser_search_methods() -> None:
    protected_routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == "/backend-api/codex/alpha/search"
    ]
    preflight_routes = [
        route
        for route in codex_preflight_router.routes
        if isinstance(route, APIRoute) and route.path == "/backend-api/codex/alpha/search"
    ]

    protected_methods = set().union(*(route.methods for route in protected_routes))
    preflight_methods = set().union(*(route.methods for route in preflight_routes))

    assert protected_methods == {"GET", "POST"}
    assert "OPTIONS" in preflight_methods
    assert {"DELETE", "HEAD", "PATCH", "PUT", "TRACE"}.issubset(preflight_methods)

    unsupported_route = next(route for route in preflight_routes if "PUT" in route.methods)
    assert unsupported_route.include_in_schema is False


def test_codex_alpha_search_openapi_operations_are_unique() -> None:
    operations = {
        (method, route.operation_id)
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == "/backend-api/codex/alpha/search"
        for method in route.methods
    }

    assert operations == {
        ("GET", "codex_alpha_search_get"),
        ("POST", "codex_alpha_search_post"),
    }

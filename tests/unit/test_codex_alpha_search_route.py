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

    methods = set().union(*(route.methods for route in protected_routes + preflight_routes))
    assert methods == {"GET", "POST", "OPTIONS"}

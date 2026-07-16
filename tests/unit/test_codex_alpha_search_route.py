from fastapi.routing import APIRoute

from app.modules.proxy.api import router


def test_codex_alpha_search_route_allows_browser_search_methods() -> None:
    routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == "/backend-api/codex/alpha/search"
    ]

    methods = set().union(*(route.methods for route in routes))
    assert methods == {"GET", "POST", "HEAD", "OPTIONS"}

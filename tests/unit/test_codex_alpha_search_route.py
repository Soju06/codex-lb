import pytest
from fastapi.routing import APIRoute

from app.modules.proxy.api import router, v1_router


@pytest.mark.parametrize("base_path", ["/backend-api/codex", "/v1"])
def test_codex_alpha_search_route_is_post_only(base_path: str) -> None:
    routes = [
        route
        for route in [*router.routes, *v1_router.routes]
        if isinstance(route, APIRoute) and route.path == f"{base_path}/alpha/search"
    ]

    assert len(routes) == 1
    assert routes[0].methods == {"POST"}

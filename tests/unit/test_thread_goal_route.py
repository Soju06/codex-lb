from fastapi.routing import APIRoute

from app.modules.proxy.api import router


def test_thread_goal_get_and_post_routes_share_handler_with_distinct_ids() -> None:
    routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == "/backend-api/codex/thread/goal/get"
    ]

    assert len(routes) == 2
    routes_by_method = {method: route for route in routes for method in route.methods}
    assert set(routes_by_method) == {"GET", "POST"}
    assert routes_by_method["GET"].endpoint is routes_by_method["POST"].endpoint
    assert routes_by_method["GET"].unique_id == "thread_goal_get_backend_api_codex_thread_goal_get_get"
    assert routes_by_method["POST"].unique_id == "thread_goal_get_backend_api_codex_thread_goal_get_post"

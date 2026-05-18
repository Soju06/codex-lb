from app.core.middleware.api_firewall import add_api_firewall_middleware
from app.core.middleware.dashboard_auth_proxy import add_dashboard_auth_proxy_middleware
from app.core.middleware.request_decompression import add_request_decompression_middleware
from app.core.middleware.request_id import add_request_id_middleware

__all__ = [
    "add_api_firewall_middleware",
    "add_dashboard_auth_proxy_middleware",
    "add_request_decompression_middleware",
    "add_request_id_middleware",
]

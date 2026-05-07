from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse

from app.core.config.settings import Settings


def requires_cluster_registration(settings: Settings) -> bool:
    if len(settings.http_responses_session_bridge_instance_ring) > 1:
        return True
    advertise_base_url = settings.http_responses_session_bridge_advertise_base_url
    if advertise_base_url is None:
        return False
    hostname = urlparse(advertise_base_url).hostname
    if hostname is None:
        return False
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        return True
    return not parsed_ip.is_loopback

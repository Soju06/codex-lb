from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy

from app.core.types import JsonObject, JsonValue
from app.modules.proxy.schemas import AdditionalRateLimitStatus, RateLimitStatusDetails
from app.modules.proxy.types import RateLimitStatusPayloadData


def compose_desktop_usage(original: JsonObject, pooled: RateLimitStatusPayloadData) -> JsonObject:
    """Replace evidenced quota while retaining the authenticated account envelope."""
    if pooled.rate_limit is None:
        raise ValueError("Desktop usage requires pooled main quota")
    result = deepcopy(dict(original))
    result["rate_limit"] = RateLimitStatusDetails.from_data(pooled.rate_limit).model_dump(mode="json")
    replacements: dict[tuple[str, str], JsonObject] = {
        (
            _limit_identity(bucket.limit_name),
            _limit_identity(bucket.metered_feature),
        ): AdditionalRateLimitStatus.from_data(bucket).model_dump(mode="json")
        for bucket in pooled.additional_rate_limits
        if _limit_identity(bucket.limit_name) not in {"", "gpt-reserve"} and bucket.rate_limit is not None
    }
    additional = result.get("additional_rate_limits")
    merged: list[JsonValue] = []
    claimed_names: set[str] = set()
    applied: dict[str, JsonObject] = {}
    if isinstance(additional, list):
        for bucket in additional:
            if isinstance(bucket, Mapping):
                identity = _limit_identity(bucket.get("limit_name"))
                claimed_names.add(identity)
                replacement = replacements.get((identity, _limit_identity(bucket.get("metered_feature"))))
                if replacement is not None:
                    bucket = {**bucket, "rate_limit": replacement["rate_limit"]}
                    applied[identity] = replacement
            merged.append(bucket)
    for (identity, _), bucket in replacements.items():
        if identity not in claimed_names:
            merged.append(bucket)
            applied[identity] = bucket
    if merged or isinstance(additional, list):
        result["additional_rate_limits"] = merged
    if pooled.rate_limit.allowed and not pooled.rate_limit.limit_reached:
        _remove_superseded_warnings(result, applied)
    return result


def _limit_identity(value: JsonValue) -> str:
    return re.sub(r"[_\s.]+", "-", value.strip().lower()) if isinstance(value, str) else ""


def _remove_superseded_warnings(result: dict[str, JsonValue], replacements: Mapping[str, JsonObject]) -> None:
    reached = result.get("rate_limit_reached_type")
    if isinstance(reached, Mapping) and reached.get("type") == "rate_limit_reached":
        result.pop("rate_limit_reached_type")
    upsell = result.get("rate_limit_upsell")
    # These are the known Desktop reserve banner and quota warning discriminators.
    # Unknown banners and credit/spend restrictions remain account-owned.
    if isinstance(upsell, Mapping) and upsell.get("banner_type") == "luna_reserve":
        result.pop("rate_limit_upsell")
    if _superseded_warning(result.get("rate_limit_warning"), replacements):
        result.pop("rate_limit_warning")
    sidebar = result.get("sidebar_usage_warnings")
    if not isinstance(sidebar, Mapping):
        return
    updated = dict(sidebar)
    if _superseded_warning(updated.get("default"), replacements):
        updated["default"] = None
    by_model = updated.get("by_model")
    if isinstance(by_model, Mapping):
        updated["by_model"] = {
            model: None if _superseded_warning(warning, replacements, model) else warning
            for model, warning in by_model.items()
        }
    result["sidebar_usage_warnings"] = updated


def _superseded_warning(value: JsonValue, replacements: Mapping[str, JsonObject], model: str | None = None) -> bool:
    if not isinstance(value, Mapping) or value.get("banner_type") != "rate_limit":
        return False
    quota = value.get("rate_limit")
    if not isinstance(quota, Mapping) or not (quota.get("allowed") is False or quota.get("limit_reached") is True):
        return False
    # Monthly and credit warnings cannot be resolved using included pool quota.
    if value.get("monthly_limit") is not None or value.get("credits") is not None:
        return False
    identity = _limit_identity(value.get("model_slug")) or _limit_identity(model)
    if not identity:
        return True
    replacement = replacements.get(identity)
    replacement_quota = replacement.get("rate_limit") if replacement is not None else None
    return (
        isinstance(replacement_quota, Mapping)
        and replacement_quota.get("allowed") is True
        and replacement_quota.get("limit_reached") is False
    )

from __future__ import annotations

from typing import Any

import pytest

from app.main import create_app

pytestmark = pytest.mark.unit

_MULTIPART_OPERATIONS = {
    ("/api/accounts/import", "post"),
    ("/backend-api/transcribe", "post"),
    ("/v1/audio/transcriptions", "post"),
    ("/v1/images/edits", "post"),
}


def _nullable_string(title: str) -> dict[str, Any]:
    return {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "title": title,
    }


def _binary(title: str) -> dict[str, Any]:
    return {
        "type": "string",
        "contentMediaType": "application/octet-stream",
        "title": title,
    }


def _binary_array(title: str) -> dict[str, Any]:
    return {
        "anyOf": [
            {
                "type": "array",
                "items": {
                    "type": "string",
                    "contentMediaType": "application/octet-stream",
                },
            },
            {"type": "null"},
        ],
        "title": title,
    }


def _request_schema(openapi: dict[str, Any], path: str) -> dict[str, Any]:
    request_body = openapi["paths"][path]["post"]["requestBody"]
    assert set(request_body) == {"content", "required"}
    assert request_body["required"] is True
    assert set(request_body["content"]) == {"multipart/form-data"}
    schema = request_body["content"]["multipart/form-data"]["schema"]
    if "$ref" not in schema:
        return schema
    component_name = schema["$ref"].rsplit("/", 1)[-1]
    return openapi["components"]["schemas"][component_name]


def test_all_fastapi_multipart_operations_have_an_explicit_bounded_policy_surface() -> None:
    openapi = create_app().openapi()
    actual = {
        (path, method)
        for path, path_item in openapi["paths"].items()
        for method, operation in path_item.items()
        if isinstance(operation, dict) and "multipart/form-data" in operation.get("requestBody", {}).get("content", {})
    }

    assert actual == _MULTIPART_OPERATIONS


def test_multipart_request_body_schemas_remain_semantically_identical() -> None:
    openapi = create_app().openapi()

    assert _request_schema(openapi, "/api/accounts/import") == {
        "type": "object",
        "title": "Body_import_account_api_accounts_import_post",
        "required": ["auth_json"],
        "properties": {"auth_json": _binary("Auth Json")},
    }
    assert _request_schema(openapi, "/backend-api/transcribe") == {
        "type": "object",
        "title": "Body_backend_transcribe_backend_api_transcribe_post",
        "required": ["file"],
        "properties": {
            "file": _binary("File"),
            "prompt": _nullable_string("Prompt"),
        },
    }
    assert _request_schema(openapi, "/v1/audio/transcriptions") == {
        "type": "object",
        "title": "Body_v1_audio_transcriptions_v1_audio_transcriptions_post",
        "required": ["model", "file"],
        "properties": {
            "model": {"type": "string", "title": "Model"},
            "file": _binary("File"),
            "prompt": _nullable_string("Prompt"),
        },
    }

    optional_image_fields = {
        "model": "Model",
        "n": "N",
        "size": "Size",
        "quality": "Quality",
        "background": "Background",
        "output_format": "Output Format",
        "output_compression": "Output Compression",
        "moderation": "Moderation",
        "partial_images": "Partial Images",
        "stream": "Stream",
        "input_fidelity": "Input Fidelity",
        "user": "User",
    }
    image_properties = {field: _nullable_string(title) for field, title in optional_image_fields.items()}
    image_properties.update(
        {
            "prompt": {"type": "string", "title": "Prompt"},
            "image": _binary_array("Image"),
            "image[]": _binary_array("Image[]"),
            "mask": {
                "anyOf": [
                    {
                        "type": "string",
                        "contentMediaType": "application/octet-stream",
                    },
                    {"type": "null"},
                ],
                "title": "Mask",
            },
        }
    )
    assert _request_schema(openapi, "/v1/images/edits") == {
        "type": "object",
        "title": "Body_v1_images_edits_v1_images_edits_post",
        "required": ["prompt"],
        "properties": image_properties,
    }


def test_non_multipart_codex_image_and_file_surfaces_are_unchanged() -> None:
    openapi = create_app().openapi()

    assert "/backend-api/codex/images/edits" not in openapi["paths"]
    files_body = openapi["paths"]["/backend-api/files"]["post"]["requestBody"]
    assert files_body == {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/FileCreateRequest"},
            }
        },
    }

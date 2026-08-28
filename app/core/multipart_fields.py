from __future__ import annotations

from collections.abc import Iterable
from typing import NoReturn

from fastapi.exceptions import RequestValidationError
from starlette.datastructures import FormData, UploadFile


def _raise_field_validation(field: str, *, error_type: str, message: str, value: object = None) -> NoReturn:
    raise RequestValidationError(
        [
            {
                "type": error_type,
                "loc": ("body", field),
                "msg": message,
                "input": value,
            }
        ]
    )


def raise_upload_validation(field: str, *, value: object = None) -> NoReturn:
    _raise_field_validation(
        field,
        error_type="value_error",
        message="Expected one uploaded file",
        value=value,
    )


def required_upload(form: FormData, field: str) -> UploadFile:
    values = form.getlist(field)
    if not values:
        _raise_field_validation(field, error_type="missing", message="Field required")
    if len(values) != 1 or not isinstance(values[0], UploadFile):
        raise_upload_validation(field, value=values)
    return values[0]


def optional_upload(form: FormData, field: str) -> UploadFile | None:
    values = form.getlist(field)
    if not values:
        return None
    if len(values) != 1 or not isinstance(values[0], UploadFile):
        raise_upload_validation(field, value=values)
    return values[0]


def required_text(form: FormData, field: str) -> str:
    value = form.get(field)
    if value is None:
        _raise_field_validation(field, error_type="missing", message="Field required")
    if not isinstance(value, str):
        _raise_field_validation(field, error_type="string_type", message="Input should be a valid string", value=value)
    return value


def optional_text(form: FormData, field: str) -> str | None:
    value = form.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        _raise_field_validation(field, error_type="string_type", message="Input should be a valid string", value=value)
    return value


def ordered_uploads(form: FormData, fields: Iterable[str]) -> list[UploadFile]:
    allowed = frozenset(fields)
    uploads: list[UploadFile] = []
    for field, value in form.multi_items():
        if field not in allowed:
            continue
        if not isinstance(value, UploadFile):
            _raise_field_validation(
                field,
                error_type="value_error",
                message="Expected an uploaded file",
                value=value,
            )
        uploads.append(value)
    return uploads


def ordered_text_items(form: FormData, *, excluded_fields: Iterable[str] = ()) -> list[tuple[str, str]]:
    excluded = frozenset(excluded_fields)
    return [(field, value) for field, value in form.multi_items() if field not in excluded and isinstance(value, str)]


def uploaded_file_items(form: FormData) -> list[tuple[str, UploadFile]]:
    return [(field, value) for field, value in form.multi_items() if isinstance(value, UploadFile)]

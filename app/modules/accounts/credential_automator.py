from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from curl_cffi import requests as curl_requests

from app.core.clients.oauth import OAuthTokens, build_authorization_url, generate_pkce_pair
from app.core.config.settings import get_settings

_TWO_FA_LIVE_BASE_URL = "https://2fa.live"
_SENTINEL_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)
_DEFAULT_ACCEPT_LANGUAGE = "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3"
_MFA_TOTP_TYPE = "totp"


class CredentialAuthorizationError(Exception):
    pass


@dataclass(frozen=True)
class _PasswordVerifyResult:
    continue_url: str | None
    factors: tuple[dict[str, str | None], ...]


class CredentialAuthAutomator:
    async def authorize(
        self,
        email: str,
        password: str,
        totp_secret: str,
    ) -> OAuthTokens:
        return await asyncio.to_thread(
            self._authorize_sync,
            email,
            password,
            totp_secret,
        )

    def _authorize_sync(
        self,
        email: str,
        password: str,
        totp_secret: str,
    ) -> OAuthTokens:
        settings = get_settings()
        auth_base_url = settings.auth_base_url.rstrip("/")
        timeout_seconds = float(settings.oauth_timeout_seconds)
        code_verifier, code_challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(24)
        authorize_url = build_authorization_url(
            state=state,
            code_challenge=code_challenge,
            base_url=auth_base_url,
            client_id=settings.oauth_client_id,
            redirect_uri=settings.oauth_redirect_uri,
            scope=_ensure_offline_access_scope(settings.oauth_scope),
        )

        session = curl_requests.Session(impersonate="chrome")
        try:
            code = self._oauth_flow_sync(
                session=session,
                authorize_url=authorize_url,
                issuer=auth_base_url,
                email=email,
                password=password,
                totp_secret=totp_secret,
                redirect_uri=settings.oauth_redirect_uri,
                expected_state=state,
                timeout_seconds=timeout_seconds,
            )
            return self._exchange_code_for_tokens_sync(
                session=session,
                issuer=auth_base_url,
                code=code,
                code_verifier=code_verifier,
                redirect_uri=settings.oauth_redirect_uri,
                client_id=settings.oauth_client_id,
                timeout_seconds=timeout_seconds,
            )
        finally:
            session.close()

    def _oauth_flow_sync(
        self,
        *,
        session: curl_requests.Session,
        authorize_url: str,
        issuer: str,
        email: str,
        password: str,
        totp_secret: str,
        redirect_uri: str,
        expected_state: str,
        timeout_seconds: float,
    ) -> str:
        browser_headers = _browser_headers()
        next_url = self._request_redirect_location(
            session=session,
            url=authorize_url,
            headers=browser_headers,
            label="Authorize redirect",
            timeout_seconds=timeout_seconds,
        )
        next_url = self._request_redirect_location(
            session=session,
            url=next_url,
            headers=browser_headers,
            label="OAuth2 auth redirect",
            timeout_seconds=timeout_seconds,
        )
        login_url = self._request_redirect_location(
            session=session,
            url=next_url,
            headers=browser_headers,
            label="Accounts login redirect",
            timeout_seconds=timeout_seconds,
        )
        self._request_success(
            session=session,
            url=login_url,
            headers=browser_headers,
            label="Login page request",
            allow_redirects=True,
            timeout_seconds=timeout_seconds,
        )

        authorize_sentinel = self._build_sentinel_header(
            session=session,
            flow="authorize_continue",
            referer=login_url,
            timeout_seconds=timeout_seconds,
        )
        continue_url = self._authorize_continue(
            session=session,
            issuer=issuer,
            login_url=login_url,
            email=email,
            sentinel_header=authorize_sentinel,
            timeout_seconds=timeout_seconds,
        )

        password_sentinel = self._build_sentinel_header(
            session=session,
            flow="password_verify",
            referer=continue_url,
            timeout_seconds=timeout_seconds,
        )
        password_result = self._verify_password(
            session=session,
            issuer=issuer,
            continue_url=continue_url,
            password=password,
            sentinel_header=password_sentinel,
            timeout_seconds=timeout_seconds,
        )
        continue_url = password_result.continue_url

        if password_result.factors:
            factor_id = _pick_mfa_factor_id(password_result.factors, expected_type=_MFA_TOTP_TYPE)
            self._issue_mfa_challenge(
                session=session,
                issuer=issuer,
                continue_url=continue_url,
                factor_id=factor_id,
                timeout_seconds=timeout_seconds,
            )
            mfa_code = self._fetch_totp_code(
                session=session,
                totp_secret=totp_secret,
                timeout_seconds=timeout_seconds,
            )
            continue_url = self._verify_mfa_challenge(
                session=session,
                issuer=issuer,
                continue_url=continue_url,
                factor_id=factor_id,
                mfa_code=mfa_code,
                timeout_seconds=timeout_seconds,
            )

        if not continue_url:
            raise CredentialAuthorizationError("Missing continue_url after password/MFA verification.")

        if "sign-in-with-chatgpt" in continue_url and "consent" in continue_url:
            self._request_success(
                session=session,
                url=continue_url,
                headers=browser_headers,
                label="Consent page request",
                allow_redirects=True,
                timeout_seconds=timeout_seconds,
            )
            workspace_id = _extract_workspace_id_from_cookie_jar(session.cookies.jar)
            if not workspace_id:
                raise CredentialAuthorizationError(
                    "Workspace ID required but could not be determined automatically.",
                )
            continue_url = self._select_workspace(
                session=session,
                issuer=issuer,
                continue_url=continue_url,
                workspace_id=workspace_id,
                timeout_seconds=timeout_seconds,
            )

        location = self._request_redirect_location(
            session=session,
            url=continue_url,
            headers=_browser_headers(referer=continue_url),
            label="OAuth2 login verifier redirect",
            timeout_seconds=timeout_seconds,
        )
        if location.startswith(redirect_uri):
            return _parse_code_from_redirect(location, expected_state=expected_state)

        consent_prefix = f"{issuer}/api/accounts/consent"
        if location.startswith(consent_prefix):
            location = self._request_redirect_location(
                session=session,
                url=location,
                headers=_browser_headers(referer=continue_url),
                label="Accounts consent redirect",
                timeout_seconds=timeout_seconds,
            )
            if location.startswith(redirect_uri):
                return _parse_code_from_redirect(location, expected_state=expected_state)

        location = self._request_redirect_location(
            session=session,
            url=location,
            headers=_browser_headers(referer=continue_url),
            label="Final authorization redirect",
            timeout_seconds=timeout_seconds,
        )
        if location.startswith(redirect_uri):
            return _parse_code_from_redirect(location, expected_state=expected_state)
        raise CredentialAuthorizationError("OAuth flow did not return authorization code redirect.")

    def _exchange_code_for_tokens_sync(
        self,
        *,
        session: curl_requests.Session,
        issuer: str,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
        timeout_seconds: float,
    ) -> OAuthTokens:
        payload = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        response = session.post(
            f"{issuer}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urlencode(payload, quote_via=quote),
            timeout=timeout_seconds,
        )
        body = self._read_json_dict(response, label="OAuth token response")
        if response.status_code >= 400:
            message = _extract_error_message(
                body,
                default=f"OAuth token request failed with status {response.status_code}.",
            )
            raise CredentialAuthorizationError(message)

        access_token = body.get("access_token")
        refresh_token = body.get("refresh_token")
        id_token = body.get("id_token")
        if not isinstance(access_token, str) or not access_token:
            raise CredentialAuthorizationError("OAuth token response missing access token.")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CredentialAuthorizationError("OAuth token response missing refresh token.")
        if not isinstance(id_token, str) or not id_token:
            raise CredentialAuthorizationError("OAuth token response missing id token.")
        return OAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
        )

    def _authorize_continue(
        self,
        *,
        session: curl_requests.Session,
        issuer: str,
        login_url: str,
        email: str,
        sentinel_header: str,
        timeout_seconds: float,
    ) -> str:
        response = self._post_continue_request(
            session=session,
            url=f"{issuer}/api/accounts/authorize/continue",
            headers={
                **_json_headers(referer=login_url, origin=issuer),
                "openai-sentinel-token": sentinel_header,
            },
            payload={"username": {"kind": "email", "value": email}},
            label="Authorize continue",
            timeout_seconds=timeout_seconds,
        )
        return _resolve_continue_url(
            continue_url=response.get("continue_url"),
            issuer=issuer,
            label="authorize/continue",
        )

    def _verify_password(
        self,
        *,
        session: curl_requests.Session,
        issuer: str,
        continue_url: str,
        password: str,
        sentinel_header: str,
        timeout_seconds: float,
    ) -> _PasswordVerifyResult:
        response = self._post_continue_request(
            session=session,
            url=f"{issuer}/api/accounts/password/verify",
            headers={
                **_json_headers(referer=continue_url, origin=issuer),
                "openai-sentinel-token": sentinel_header,
            },
            payload={"password": password},
            label="Password verify",
            timeout_seconds=timeout_seconds,
        )
        response_continue_url = response.get("continue_url")
        parsed_continue_url = (
            _resolve_continue_url(
                continue_url=response_continue_url,
                issuer=issuer,
                label="password/verify",
            )
            if isinstance(response_continue_url, str) and response_continue_url
            else None
        )

        factors: tuple[dict[str, str | None], ...] = ()
        page = response.get("page")
        if isinstance(page, dict) and page.get("type") == "mfa_challenge":
            payload = page.get("payload")
            raw_factors = payload.get("factors") if isinstance(payload, dict) else None
            if isinstance(raw_factors, list):
                parsed_factors: list[dict[str, str | None]] = []
                for raw_factor in raw_factors:
                    if not isinstance(raw_factor, dict):
                        continue
                    factor_id = raw_factor.get("id")
                    factor_type = raw_factor.get("factor_type")
                    parsed_factors.append(
                        {
                            "id": factor_id if isinstance(factor_id, str) else None,
                            "factor_type": factor_type if isinstance(factor_type, str) else None,
                        },
                    )
                factors = tuple(parsed_factors)
        return _PasswordVerifyResult(continue_url=parsed_continue_url, factors=factors)

    def _issue_mfa_challenge(
        self,
        *,
        session: curl_requests.Session,
        issuer: str,
        continue_url: str | None,
        factor_id: str,
        timeout_seconds: float,
    ) -> None:
        if not continue_url:
            raise CredentialAuthorizationError("Missing continue_url before issuing MFA challenge.")
        self._post_continue_request(
            session=session,
            url=f"{issuer}/api/accounts/mfa/issue_challenge",
            headers=_json_headers(referer=continue_url, origin=issuer),
            payload={"id": factor_id, "type": _MFA_TOTP_TYPE, "force_fresh_challenge": False},
            label="MFA issue challenge",
            require_continue_url=False,
            timeout_seconds=timeout_seconds,
        )

    def _verify_mfa_challenge(
        self,
        *,
        session: curl_requests.Session,
        issuer: str,
        continue_url: str | None,
        factor_id: str,
        mfa_code: str,
        timeout_seconds: float,
    ) -> str:
        if not continue_url:
            raise CredentialAuthorizationError("Missing continue_url before MFA verify.")
        response = self._post_continue_request(
            session=session,
            url=f"{issuer}/api/accounts/mfa/verify",
            headers=_json_headers(referer=continue_url, origin=issuer),
            payload={"id": factor_id, "type": _MFA_TOTP_TYPE, "code": mfa_code},
            label="MFA verify",
            timeout_seconds=timeout_seconds,
        )
        return _resolve_continue_url(
            continue_url=response.get("continue_url"),
            issuer=issuer,
            label="mfa/verify",
        )

    def _select_workspace(
        self,
        *,
        session: curl_requests.Session,
        issuer: str,
        continue_url: str,
        workspace_id: str,
        timeout_seconds: float,
    ) -> str:
        response = self._post_continue_request(
            session=session,
            url=f"{issuer}/api/accounts/workspace/select",
            headers=_json_headers(referer=continue_url, origin=issuer),
            payload={"workspace_id": workspace_id},
            label="Workspace select",
            timeout_seconds=timeout_seconds,
        )
        return _resolve_continue_url(
            continue_url=response.get("continue_url"),
            issuer=issuer,
            label="workspace/select",
        )

    def _post_continue_request(
        self,
        *,
        session: curl_requests.Session,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        label: str,
        timeout_seconds: float,
        require_continue_url: bool = True,
    ) -> dict[str, Any]:
        response = session.post(
            url,
            headers=headers,
            json=payload,
            allow_redirects=True,
            timeout=timeout_seconds,
        )
        body = self._read_json_dict(response, label=label)
        if response.status_code >= 400:
            message = _extract_error_message(
                body,
                default=f"{label} failed with status {response.status_code}.",
            )
            raise CredentialAuthorizationError(message)
        if require_continue_url:
            continue_url = body.get("continue_url")
            if not isinstance(continue_url, str) or not continue_url:
                raise CredentialAuthorizationError(f"{label} response missing continue_url.")
        return body

    def _build_sentinel_header(
        self,
        *,
        session: curl_requests.Session,
        flow: str,
        referer: str,
        timeout_seconds: float,
    ) -> str:
        p_value = _b64url(os.urandom(32))
        token = self._fetch_sentinel_token(
            session=session,
            p_value=p_value,
            referer=referer,
            timeout_seconds=timeout_seconds,
        )
        did = _cookie_value_from_cookie_jar(
            session.cookies.jar,
            cookie_name="oai-did",
            domain_suffix="auth.openai.com",
        ) or ""
        return _build_sentinel_header_value(
            p_value=p_value,
            token=token,
            device_id=did,
            flow=flow,
        )

    def _fetch_sentinel_token(
        self,
        *,
        session: curl_requests.Session,
        p_value: str,
        referer: str,
        timeout_seconds: float,
    ) -> str:
        response = session.post(
            _SENTINEL_URL,
            headers={
                "Accept": "*/*",
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "https://sentinel.openai.com",
                "Referer": referer,
                "User-Agent": _DEFAULT_USER_AGENT,
                "Accept-Language": _DEFAULT_ACCEPT_LANGUAGE,
            },
            data=json.dumps({"p": p_value}),
            allow_redirects=True,
            timeout=timeout_seconds,
        )
        payload = self._read_json_dict(response, label="Sentinel request")
        if response.status_code >= 400:
            message = _extract_error_message(payload, default=f"Sentinel request failed ({response.status_code}).")
            raise CredentialAuthorizationError(message)
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise CredentialAuthorizationError("Sentinel response missing token.")
        return token

    def _fetch_totp_code(
        self,
        *,
        session: curl_requests.Session,
        totp_secret: str,
        timeout_seconds: float,
    ) -> str:
        response = session.get(
            f"{_TWO_FA_LIVE_BASE_URL}/tok/{quote(totp_secret, safe='')}",
            headers={"Accept": "*/*", "User-Agent": _DEFAULT_USER_AGENT},
            allow_redirects=True,
            timeout=min(timeout_seconds, 30.0),
        )
        payload = self._read_json_dict(response, label="OTP provider request")
        if response.status_code >= 400:
            message = _extract_error_message(
                payload,
                default=f"OTP provider request failed with status {response.status_code}.",
            )
            raise CredentialAuthorizationError(message)
        token = payload.get("token")
        if not isinstance(token, str) or not token.strip():
            raise CredentialAuthorizationError("OTP provider returned an empty token.")
        return token.strip()

    def _request_redirect_location(
        self,
        *,
        session: curl_requests.Session,
        url: str,
        headers: dict[str, str],
        label: str,
        timeout_seconds: float,
    ) -> str:
        response = session.get(
            url,
            headers=headers,
            allow_redirects=False,
            timeout=timeout_seconds,
        )
        if response.status_code >= 400:
            detail = self._read_error_detail(response)
            raise CredentialAuthorizationError(f"{label} failed ({response.status_code}): {detail}")
        location = response.headers.get("Location")
        if not location:
            raise CredentialAuthorizationError(f"{label} did not return Location header.")
        return urljoin(str(response.url), location)

    def _request_success(
        self,
        *,
        session: curl_requests.Session,
        url: str,
        headers: dict[str, str],
        label: str,
        allow_redirects: bool,
        timeout_seconds: float,
    ) -> None:
        response = session.get(
            url,
            headers=headers,
            allow_redirects=allow_redirects,
            timeout=timeout_seconds,
        )
        if response.status_code >= 400:
            detail = self._read_error_detail(response)
            raise CredentialAuthorizationError(f"{label} failed ({response.status_code}): {detail}")

    def _read_error_detail(self, response: curl_requests.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return _extract_error_message(payload, default=f"HTTP {response.status_code}")
        except Exception:
            pass
        body = response.text.strip()
        if not body:
            return f"HTTP {response.status_code}"
        return body[:240]

    def _read_json_dict(self, response: curl_requests.Response, *, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            body = response.text.strip()
            suffix = f": {body[:240]}" if body else ""
            raise CredentialAuthorizationError(f"{label} returned non-JSON response{suffix}") from exc
        if not isinstance(payload, dict):
            raise CredentialAuthorizationError(f"{label} returned invalid JSON payload.")
        return payload


def _extract_error_message(payload: dict[str, Any], *, default: str) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "error_description", "description", "error"):
            candidate = error.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    elif isinstance(error, str) and error.strip():
        return error.strip()

    for key in ("message", "error_description", "description"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return default


def _resolve_continue_url(*, continue_url: str | None, issuer: str, label: str) -> str:
    if not continue_url:
        raise CredentialAuthorizationError(f"{label} response missing continue_url.")
    return urljoin(f"{issuer}/", continue_url)


def _ensure_offline_access_scope(scope: str) -> str:
    tokens = scope.split()
    if "offline_access" in tokens:
        return scope
    if not scope.strip():
        return "offline_access"
    return f"{scope} offline_access"


def _browser_headers(
    *,
    referer: str | None = None,
    user_agent: str = _DEFAULT_USER_AGENT,
    accept_language: str = _DEFAULT_ACCEPT_LANGUAGE,
) -> dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": user_agent,
        "Accept-Language": accept_language,
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _json_headers(
    *,
    referer: str | None,
    origin: str,
    user_agent: str = _DEFAULT_USER_AGENT,
    accept_language: str = _DEFAULT_ACCEPT_LANGUAGE,
) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": origin,
        "User-Agent": user_agent,
        "Accept-Language": accept_language,
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _build_sentinel_header_value(
    *,
    p_value: str,
    token: str,
    device_id: str,
    flow: str,
) -> str:
    hashed_payload = _b64url(hashlib.sha256(p_value.encode("ascii")).digest())
    payload = {
        "p": p_value,
        "t": hashed_payload,
        "c": token,
        "id": device_id,
        "flow": flow,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _pick_mfa_factor_id(
    factors: tuple[dict[str, str | None], ...] | list[dict[str, str | None]],
    *,
    expected_type: str,
) -> str:
    for factor in factors:
        factor_type = factor.get("factor_type")
        factor_id = factor.get("id")
        if factor_type == expected_type and isinstance(factor_id, str) and factor_id:
            return factor_id
    raise CredentialAuthorizationError(f"MFA factor type '{expected_type}' not found.")


def _parse_code_from_redirect(redirect_url: str, *, expected_state: str) -> str:
    parsed = urlparse(redirect_url)
    query = parse_qs(parsed.query)
    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]
    if not isinstance(code, str) or not code:
        raise CredentialAuthorizationError("Redirect does not contain authorization code.")
    if state != expected_state:
        raise CredentialAuthorizationError("State mismatch in OAuth redirect.")
    return code


def _extract_workspace_id_from_cookie_jar(cookie_jar: CookieJar) -> str | None:
    raw_cookie = _cookie_value_from_cookie_jar(
        cookie_jar,
        cookie_name="oai-client-auth-session",
        domain_suffix="auth.openai.com",
    )
    if not raw_cookie:
        return None
    return _extract_workspace_id_from_cookie_value(raw_cookie)


def _cookie_value_from_cookie_jar(
    cookie_jar: CookieJar,
    *,
    cookie_name: str,
    domain_suffix: str,
) -> str | None:
    normalized_suffix = domain_suffix.lstrip(".").lower()
    for cookie in cookie_jar:
        if cookie.name != cookie_name:
            continue
        raw_domain = (cookie.domain or "").lstrip(".").lower()
        if not raw_domain:
            continue
        if raw_domain == normalized_suffix or raw_domain.endswith(f".{normalized_suffix}"):
            value = cookie.value
            if isinstance(value, str) and value:
                return value
    return None


def _extract_workspace_id_from_cookie_value(raw_cookie: str) -> str | None:
    payload = _decode_cookie_json(raw_cookie)
    if not payload:
        return None

    workspaces = _extract_workspaces(payload)
    personal_workspace_id: str | None = None
    first_workspace_id: str | None = None
    for workspace in workspaces:
        workspace_id = workspace.get("id")
        if not isinstance(workspace_id, str) or not workspace_id:
            continue
        if first_workspace_id is None:
            first_workspace_id = workspace_id
        kind = workspace.get("kind")
        if kind == "personal":
            personal_workspace_id = workspace_id
            break
    return personal_workspace_id or first_workspace_id


def _extract_workspaces(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_workspaces = payload.get("workspaces")
    if isinstance(raw_workspaces, list):
        return [item for item in raw_workspaces if isinstance(item, dict)]

    for key in ("data", "session", "payload"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_workspaces = nested.get("workspaces")
            if isinstance(nested_workspaces, list):
                return [item for item in nested_workspaces if isinstance(item, dict)]
    return []


def _decode_cookie_json(raw_cookie: str) -> dict[str, Any] | None:
    if not raw_cookie:
        return None

    parts = raw_cookie.split(".")
    candidates: list[str] = []
    if len(parts) >= 1:
        candidates.append(parts[0])
    if len(parts) >= 2:
        candidates.append(parts[1])
    if len(parts) >= 3:
        candidates.append(parts[2])
    candidates.append(raw_cookie)

    for candidate in candidates:
        try:
            padded = candidate + "=" * (-len(candidate) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            parsed = json.loads(decoded)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None

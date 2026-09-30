"""The OIDC connection document: what an operator may write, and what comes back.

Every value here is a URL the server will later fetch or a browser will later
follow, so the validation is the first security boundary of the whole flow.
"""

from __future__ import annotations

import json

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import DashboardAuthProvider
from app.modules.auth_providers.config import (
    OIDC_CALLBACK_PATH,
    InvalidProviderConfigError,
    build_oidc_config,
    default_discovery_url,
    load_oidc_config,
    mask_oidc_config,
    oidc_config_fingerprint,
    seal_oidc_config,
    validate_https_url,
)
from tests.fixtures.fake_idp import CLIENT_SECRET, DISCOVERY_URL, ISSUER, REDIRECT_URI, provider_config_document

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "http://idp.example.test",
        "https://user:pass@idp.example.test",
        "https://idp.example.test/#fragment",
        "https://127.0.0.1",
        "https://[::1]",
        "https://169.254.169.254",
        "https://10.1.2.3",
        "https://192.168.0.10",
        "https://172.16.9.9",
        "https://[fd00::1]",
        "https://localhost",
        "https://auth.localhost",
        "https://metadata.google.internal",
        "https://metadata.google.internal./computeMetadata/v1",
        "ftp://idp.example.test",
        "",
    ],
)
def test_refused_urls_name_their_field(url: str) -> None:
    with pytest.raises(InvalidProviderConfigError) as refusal:
        validate_https_url(url, field="issuer")
    assert refusal.value.param == "issuer"
    assert refusal.value.code == "invalid_provider_config"


@pytest.mark.parametrize(
    "url",
    [
        "https://idp.example.test",
        "https://idp.example.test/realms/main",
        "https://login.microsoftonline.com/tenant/v2.0",
        "https://8.8.8.8/auth",
    ],
)
def test_accepted_urls(url: str) -> None:
    assert validate_https_url(url, field="issuer") == url


@pytest.mark.parametrize(
    "host",
    [
        "2130706433",
        "0177.0.0.1",
        "0x7f.0.0.1",
        "127.1",
        "2852039166",
        "0251.0376.0251.0376",
        "0xa9fea9fe",
        "0",
        "[::ffff:127.0.0.1]",
        "[::ffff:169.254.169.254]",
    ],
    ids=[
        "loopback-decimal",
        "loopback-octal",
        "loopback-hex",
        "loopback-short",
        "metadata-decimal",
        "metadata-octal",
        "metadata-hex",
        "unspecified-short",
        "loopback-v4-mapped",
        "metadata-v4-mapped",
    ],
)
def test_a_refused_address_is_refused_in_every_spelling(host: str) -> None:
    """``getaddrinfo`` accepts more spellings of an address than ``ipaddress`` parses.

    A check that refuses ``169.254.169.254`` and accepts ``2852039166`` is not a
    check: the resolver hands the socket the same address either way. These are
    the spellings ``inet_aton`` -- the parser the resolver reaches for first --
    turns into loopback, the link-local metadata endpoint, or ``0.0.0.0``.
    """

    with pytest.raises(InvalidProviderConfigError) as refusal:
        validate_https_url(f"https://{host}/realms/main", field="discovery_url")
    assert refusal.value.param == "discovery_url"


@pytest.mark.parametrize(
    "host",
    ["8.8.8.8", "1.2.3.4", "idp.example.test", "login.example.test", "0x08080808"],
    ids=["dotted-quad", "dotted-quad-low", "name", "subdomain", "public-address-in-hex"],
)
def test_a_public_address_survives_the_spelling_check(host: str) -> None:
    """The normalisation decides what an address *is*; it refuses nothing new.

    A name ``inet_aton`` cannot parse is still a name, and a numeric spelling of
    a public address is still public.
    """

    assert validate_https_url(f"https://{host}/x", field="issuer") == f"https://{host}/x"


def test_the_issuer_may_not_carry_a_query() -> None:
    assert validate_https_url("https://idp.example.test/a?b=c", field="discovery_url")
    with pytest.raises(InvalidProviderConfigError):
        validate_https_url("https://idp.example.test/a?b=c", field="issuer", allow_query=False)


def test_the_discovery_url_defaults_to_the_well_known_path() -> None:
    config = build_oidc_config(provider_config_document(discovery_url=None))
    assert config.discovery_url == default_discovery_url(ISSUER)


@pytest.mark.parametrize(
    "url",
    [
        "https://[broken",
        "https://[::1",
        "https://idp.example.test:port]",
        "https://[fe80::1%25eth0",
        # ``SplitResult.port`` is lazy: these three parse silently and raise
        # only when the port is read, which nothing downstream of the write
        # does -- so unchecked they are stored and surface later as an
        # unreachable identity provider rather than as the field that is wrong.
        "https://idp.example.test:port",
        "https://idp.example.test:-1",
        "https://idp.example.test:99999",
    ],
    ids=[
        "unterminated-bracket",
        "unterminated-v6",
        "stray-bracket",
        "unterminated-zone",
        "non-numeric-port",
        "negative-port",
        "out-of-range-port",
    ],
)
def test_a_url_the_parser_cannot_read_is_a_field_refusal_not_a_crash(url: str) -> None:
    """``urlsplit`` raises on a handful of shapes; unhandled, they answer 500.

    The one typo that crashes the endpoint would be the one whose error message
    could not name the field, which is the opposite of what a connect wizard
    needs.
    """

    with pytest.raises(InvalidProviderConfigError) as refusal:
        validate_https_url(url, field="issuer")
    assert refusal.value.param == "issuer"
    assert refusal.value.code == "invalid_provider_config"


@pytest.mark.parametrize("field", ["issuer", "discovery_url", "redirect_uri"])
def test_an_unparseable_url_is_refused_by_name_from_the_whole_document(field: str) -> None:
    with pytest.raises(InvalidProviderConfigError) as refusal:
        build_oidc_config(provider_config_document(**{field: "https://[broken"}))
    assert refusal.value.param == field


def test_the_fingerprint_names_the_document_and_nothing_else() -> None:
    """Two writes inside one second are two configurations; a re-save is one."""

    base = build_oidc_config(provider_config_document())
    assert oidc_config_fingerprint(base) == oidc_config_fingerprint(build_oidc_config(provider_config_document()))
    for changed in (
        {"issuer": "https://other-idp.example.test"},
        {"client_id": "-".join(["other", "client"])},
        {"client_secret": "-".join(["rotated", "placeholder"])},
        {"groups_claim": "roles"},
    ):
        other = build_oidc_config(provider_config_document(**changed, discovery_url=DISCOVERY_URL))
        assert oidc_config_fingerprint(other) != oidc_config_fingerprint(base)
    # It is a digest, so it carries neither the secret nor anything readable.
    assert CLIENT_SECRET not in oidc_config_fingerprint(base)
    assert len(oidc_config_fingerprint(base)) == 64


def test_the_redirect_uri_must_be_this_flow_s_callback() -> None:
    with pytest.raises(InvalidProviderConfigError) as refusal:
        build_oidc_config(provider_config_document(redirect_uri="https://dash.example.test/elsewhere"))
    assert refusal.value.param == "redirect_uri"
    assert OIDC_CALLBACK_PATH in str(refusal.value)


def test_the_redirect_uri_may_not_carry_a_query() -> None:
    """Like the issuer, and for a sharper reason: it is compared byte-for-byte.

    RFC 6749 requires the token request to repeat the authorization request's
    redirect URI exactly, and the identity provider compares its registered
    value the same way. A query on it is a value that will not match something,
    somewhere, and the flow would fail at the exchange rather than at the field.
    """

    with pytest.raises(InvalidProviderConfigError) as refusal:
        build_oidc_config(provider_config_document(redirect_uri=f"{REDIRECT_URI}?next=/dashboard"))
    assert refusal.value.param == "redirect_uri"
    assert refusal.value.code == "invalid_provider_config"


@pytest.mark.parametrize("field", ["issuer", "client_id", "client_secret", "redirect_uri"])
def test_a_missing_connection_field_is_refused_by_name(field: str) -> None:
    document = provider_config_document()
    document.pop(field)
    with pytest.raises(InvalidProviderConfigError) as refusal:
        build_oidc_config(document)
    assert refusal.value.param == field


def test_claim_names_default_and_may_be_overridden() -> None:
    defaults = build_oidc_config(provider_config_document())
    assert (defaults.subject_claim, defaults.email_claim) == ("sub", "email")
    assert (defaults.name_claim, defaults.groups_claim) == ("name", "groups")

    custom = build_oidc_config(provider_config_document(groups_claim="roles", name_claim="preferred_username"))
    assert custom.groups_claim == "roles"
    assert custom.name_claim == "preferred_username"

    with pytest.raises(InvalidProviderConfigError) as refusal:
        build_oidc_config(provider_config_document(groups_claim="two words"))
    assert refusal.value.param == "groups_claim"


def test_the_document_round_trips_through_the_one_encryptor() -> None:
    config = build_oidc_config(provider_config_document())
    sealed = seal_oidc_config(config)
    assert CLIENT_SECRET.encode() not in sealed

    provider = DashboardAuthProvider(kind="oidc", provider_key="default", label="SSO", config_encrypted=sealed)
    assert load_oidc_config(provider) == config

    opened = json.loads(TokenEncryptor().decrypt(sealed))
    assert opened["issuer"] == ISSUER and opened["redirect_uri"] == REDIRECT_URI


def test_an_unreadable_or_absent_blob_reads_as_no_configuration() -> None:
    assert load_oidc_config(DashboardAuthProvider(kind="oidc", provider_key="default", label="SSO")) is None
    assert (
        load_oidc_config(
            DashboardAuthProvider(kind="oidc", provider_key="default", label="SSO", config_encrypted=b"not-sealed")
        )
        is None
    )
    # A blob that opens but no longer validates (an issuer that was allowed by
    # an older rule, say) is the same "cannot sign anyone in" answer.
    repointed = TokenEncryptor().encrypt(json.dumps({"issuer": "http://idp.example.test"}))
    assert (
        load_oidc_config(
            DashboardAuthProvider(kind="oidc", provider_key="default", label="SSO", config_encrypted=repointed)
        )
        is None
    )


def test_the_masked_projection_never_carries_the_secret() -> None:
    masked = mask_oidc_config(build_oidc_config(provider_config_document()))
    assert masked["clientSecret"] == f"****{CLIENT_SECRET[-4:]}"
    assert CLIENT_SECRET not in json.dumps(masked)
    # The client id is public by specification -- it travels in every
    # authorization URL -- and the wizard has to show it.
    assert masked["clientId"] and masked["issuer"] == ISSUER
    assert masked["redirectUri"] == REDIRECT_URI


def test_a_short_secret_is_masked_whole() -> None:
    config = build_oidc_config(provider_config_document(client_secret="abcd"))
    assert mask_oidc_config(config)["clientSecret"] == "****"

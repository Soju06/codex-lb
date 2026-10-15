"""Discovery, key handling, ID-token verification and claims mapping.

No database and no network: the identity provider is
``tests.fixtures.fake_idp``, which replaces the single outbound seam and signs
real tokens with real keys, so the algorithm-confusion cases below are the
actual attacks rather than mocks of them.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import Request

from app.core.auth.providers.oidc import (
    OIDC_CLOCK_SKEW_SECONDS,
    OidcMetadataError,
    OidcTokenError,
    fetch_metadata,
    get_oidc_metadata_cache,
    get_oidc_provider,
    hash_flow_value,
    identity_from_claims,
    verify_id_token,
)
from app.db.models import DashboardAuthProvider
from app.modules.auth_providers.config import build_oidc_config, oidc_config_fingerprint
from tests.fixtures.fake_idp import (
    ABSENT,
    CLIENT_ID,
    DISCOVERY_URL,
    ISSUER,
    JWKS_URI,
    TOKEN_ENDPOINT,
    FakeIdp,
    FakeResponse,
    SigningKey,
    json_response,
    provider_config_document,
)

pytestmark = pytest.mark.unit

NONCE = "flow-nonce-value"
NONCE_HASH = hash_flow_value(NONCE)


def _config(**overrides: object) -> Any:
    return build_oidc_config(provider_config_document(**overrides))


def _provider_row(updated_at: str = "2026-09-13T00:00:00") -> DashboardAuthProvider:
    return DashboardAuthProvider(
        id="provider-id",
        kind="oidc",
        provider_key="default",
        enabled=True,
        label="SSO",
        updated_at=datetime.fromisoformat(updated_at).replace(tzinfo=UTC),
    )


class _Clock:
    """A stand-in for the module's ``time`` so cache ages are exact, not slept."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return time.time()

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    import app.core.auth.providers.oidc as oidc_module

    fake = _Clock()
    monkeypatch.setattr(oidc_module, "time", fake)
    return fake


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> FakeIdp:
    return FakeIdp().install(monkeypatch)


# --- discovery ------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_reads_the_endpoints_and_the_keys(idp: FakeIdp) -> None:
    metadata = await fetch_metadata(_config())

    assert metadata.token_endpoint == TOKEN_ENDPOINT and metadata.jwks_uri == JWKS_URI
    assert [key["kid"] for key in metadata.keys] == ["key-1"]
    # Redirects are never followed: a redirect is how a host that passed
    # validation becomes one that would not.
    assert all(request.allow_redirects is False for request in idp.requests)


@pytest.mark.asyncio
async def test_a_document_naming_another_issuer_is_refused(idp: FakeIdp) -> None:
    idp.discovery_overrides["issuer"] = "https://evil.example.test"
    with pytest.raises(OidcMetadataError):
        await fetch_metadata(_config())


@pytest.mark.asyncio
async def test_an_endpoint_the_url_rules_refuse_is_refused(idp: FakeIdp) -> None:
    idp.discovery_overrides["jwks_uri"] = "http://169.254.169.254/keys"
    with pytest.raises(OidcMetadataError):
        await fetch_metadata(_config())


@pytest.mark.asyncio
async def test_keys_on_another_host_than_the_issuer_are_allowed(idp: FakeIdp) -> None:
    """Google Workspace publishes its keys away from its issuer; same-origin would break it."""

    elsewhere = "https://www.googleapis.test/oauth2/v3/certs"
    idp.discovery_overrides["jwks_uri"] = elsewhere
    idp.responses[elsewhere] = json_response(idp.jwks_document())
    assert (await fetch_metadata(_config())).jwks_uri == elsewhere


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status=302, body=b""),
        FakeResponse(content_type="text/html", body=b"<html></html>"),
        FakeResponse(body=b"not json"),
        FakeResponse(body=b'{"padding": "' + b"x" * (256 * 1024) + b'"}'),
    ],
    ids=["redirect", "html", "invalid-json", "oversized"],
)
@pytest.mark.asyncio
async def test_an_untrustworthy_discovery_response_is_a_failure(idp: FakeIdp, response: FakeResponse) -> None:
    idp.responses[DISCOVERY_URL] = response
    with pytest.raises(OidcMetadataError):
        await fetch_metadata(_config())


@pytest.mark.asyncio
async def test_a_key_set_larger_than_the_cap_is_refused(idp: FakeIdp) -> None:
    idp.responses[JWKS_URI] = json_response({"keys": [{"kty": "RSA", "kid": str(index)} for index in range(21)]})
    with pytest.raises(OidcMetadataError):
        await fetch_metadata(_config())


@pytest.mark.asyncio
async def test_a_document_delivered_in_pieces_is_read_whole(idp: FakeIdp) -> None:
    """A response body arrives in network-sized writes, not in one.

    ``StreamReader.read(n)`` answers with what has arrived; a single call on a
    document split across two writes returns a prefix, which parses as
    truncated JSON and fails the fetch for no reason at all.
    """

    idp.responses[DISCOVERY_URL] = json_response(idp.discovery_document(), chunk_size=16)
    idp.responses[JWKS_URI] = json_response(idp.jwks_document(), chunk_size=16)

    metadata = await fetch_metadata(_config())

    assert metadata.token_endpoint == TOKEN_ENDPOINT
    assert [key["kid"] for key in metadata.keys] == ["key-1"]


@pytest.mark.asyncio
async def test_a_body_over_the_cap_is_still_refused_when_it_arrives_in_pieces(idp: FakeIdp) -> None:
    """Reading to EOF must not become reading without a bound."""

    idp.responses[DISCOVERY_URL] = FakeResponse(body=b'{"padding": "' + b"x" * (256 * 1024) + b'"}', chunk_size=4096)
    with pytest.raises(OidcMetadataError):
        await fetch_metadata(_config())


@pytest.mark.asyncio
async def test_a_key_set_with_nothing_to_verify_with_is_refused(idp: FakeIdp) -> None:
    encryption_only = dict(idp.keys[0].jwk(), use="enc")
    idp.responses[JWKS_URI] = json_response({"keys": [encryption_only]})
    with pytest.raises(OidcMetadataError):
        await fetch_metadata(_config())


# --- the cache ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_cache_serves_a_quarter_hour_then_refetches(idp: FakeIdp, clock: _Clock) -> None:
    cache, row, config = get_oidc_metadata_cache(), _provider_row(), _config()

    await cache.get(row, config)
    await cache.get(row, config)
    assert idp.metadata_fetches == 1

    clock.advance(901)
    await cache.get(row, config)
    assert idp.metadata_fetches == 2


OTHER_ISSUER = "https://other-idp.example.test"


def _second_identity_provider(idp: FakeIdp) -> Any:
    """A second identity provider on the same fake transport, and the config naming it."""

    discovery = f"{OTHER_ISSUER}/.well-known/openid-configuration"
    idp.responses[discovery] = json_response(
        {
            "issuer": OTHER_ISSUER,
            "authorization_endpoint": f"{OTHER_ISSUER}/authorize",
            "token_endpoint": f"{OTHER_ISSUER}/token",
            "jwks_uri": f"{OTHER_ISSUER}/jwks",
        }
    )
    idp.responses[f"{OTHER_ISSUER}/jwks"] = json_response(idp.jwks_document())
    return _config(issuer=OTHER_ISSUER, discovery_url=discovery)


@pytest.mark.asyncio
async def test_a_repoint_within_one_second_is_never_served_from_the_previous_issuer(
    idp: FakeIdp, clock: _Clock
) -> None:
    """The key is the connection document, not the row's ``updated_at``.

    That column has one-second resolution on SQLite, so two configuration
    writes inside the same second share it. Keyed on it, a repointed provider
    would keep answering with the *previous* identity provider's token endpoint
    — and the exchange posts the new client secret there before any issuer
    check could refuse the answer.
    """

    cache, row = get_oidc_metadata_cache(), _provider_row()
    assert (await cache.get(row, _config())).token_endpoint == TOKEN_ENDPOINT

    # Same row object, same timestamp, different document.
    repointed = await cache.get(row, _second_identity_provider(idp))

    assert repointed.token_endpoint == f"{OTHER_ISSUER}/token"
    assert repointed.issuer == OTHER_ISSUER
    # A rotated client secret alone is a different connection too: the proof
    # that the old one worked says nothing about the new one.
    rotated = await cache.get(row, _config(client_secret="-".join(["rotated", "placeholder"])))
    assert rotated.token_endpoint == TOKEN_ENDPOINT
    assert idp.metadata_fetches == 2  # the original fetch, and the rotated document's


@pytest.mark.asyncio
async def test_an_unrelated_row_write_keeps_serving_the_same_document(idp: FakeIdp, clock: _Clock) -> None:
    """A label edit bumps ``updated_at`` and changes nothing this cache holds."""

    cache, config = get_oidc_metadata_cache(), _config()
    await cache.get(_provider_row(), config)
    await cache.get(_provider_row("2026-09-13T00:05:00"), config)
    assert idp.metadata_fetches == 1


@pytest.mark.asyncio
async def test_a_rotated_key_converges_on_the_first_token_that_uses_it(idp: FakeIdp, clock: _Clock) -> None:
    cache, row, config = get_oidc_metadata_cache(), _provider_row(), _config()
    await cache.get(row, config)

    idp.keys.append(SigningKey.rsa("key-2"))
    refreshed = await cache.refresh_for_kid(row, config, "key-2")

    assert refreshed is not None and refreshed.key_for("key-2") is not None
    assert idp.metadata_fetches == 2
    # And the refreshed set is what the cache serves from then on.
    assert (await cache.get(row, config)).key_for("key-2") is not None
    assert idp.metadata_fetches == 2


@pytest.mark.asyncio
async def test_forged_key_ids_buy_at_most_one_refresh_a_minute(idp: FakeIdp, clock: _Clock) -> None:
    cache, row, config = get_oidc_metadata_cache(), _provider_row(), _config()
    await cache.get(row, config)

    refusals = [await cache.refresh_for_kid(row, config, f"forged-{index}") for index in range(20)]

    assert refusals == [None] * 20
    assert idp.metadata_fetches == 2  # the original fetch and exactly one forced refresh

    clock.advance(61)
    assert await cache.refresh_for_kid(row, config, "forged-again") is None
    assert idp.metadata_fetches == 3


@pytest.mark.asyncio
async def test_a_second_caller_meeting_the_same_rotated_key_is_not_refused_by_the_cooldown(
    idp: FakeIdp, clock: _Clock
) -> None:
    """Two callbacks in flight meet one rotation; both must be able to verify.

    The first forces the refresh and arms the cooldown. The second asks for the
    very same ``kid`` — which the refreshed entry now holds — so answering it
    from the cooldown would refuse a token signed by a key this replica has in
    hand, turning a published rotation into one failed sign-in per minute.
    """

    cache, row, config = get_oidc_metadata_cache(), _provider_row(), _config()
    await cache.get(row, config)
    idp.keys.append(SigningKey.rsa("key-2"))

    first = await cache.refresh_for_kid(row, config, "key-2")
    second = await cache.refresh_for_kid(row, config, "key-2")

    assert first is not None and first.key_for("key-2") is not None
    assert second is not None and second.key_for("key-2") is not None
    # And still exactly one refresh: the second was answered from the cache.
    assert idp.metadata_fetches == 2
    # A ``kid`` that really is unknown still buys nothing while cooling down.
    assert await cache.refresh_for_kid(row, config, "forged") is None
    assert idp.metadata_fetches == 2


@pytest.mark.asyncio
async def test_a_brief_outage_serves_the_cached_keys_and_a_long_one_does_not(idp: FakeIdp, clock: _Clock) -> None:
    cache, row, config = get_oidc_metadata_cache(), _provider_row(), _config()
    await cache.get(row, config)
    idp.fail_metadata = True

    clock.advance(3600)
    assert (await cache.get(row, config)).issuer == ISSUER

    clock.advance(24 * 3600)
    with pytest.raises(OidcMetadataError):
        await cache.get(row, config)


@pytest.mark.asyncio
async def test_a_failing_discovery_endpoint_is_retried_on_a_backoff_not_on_every_request(
    idp: FakeIdp, clock: _Clock
) -> None:
    """A broken discovery endpoint must not cost every sign-in a fresh timeout.

    Each attempt runs under the one cache lock, so without a backoff a
    ten-second discovery timeout is added to every start and every callback,
    serialised — the token endpoint stays healthy and the sign-in path stops
    anyway.
    """

    cache, row, config = get_oidc_metadata_cache(), _provider_row(), _config()
    await cache.get(row, config)
    idp.fail_metadata = True
    clock.advance(901)

    assert (await cache.get(row, config)).issuer == ISSUER
    attempts = idp.metadata_fetches
    for _ in range(10):
        assert (await cache.get(row, config)).issuer == ISSUER
    assert idp.metadata_fetches == attempts  # every one of them served, none attempted

    clock.advance(61)
    assert (await cache.get(row, config)).issuer == ISSUER
    assert idp.metadata_fetches == attempts + 1

    # The backoff is not an extension of the trust ceiling: that is still
    # measured from the last *successful* fetch.
    clock.advance(24 * 3600)
    with pytest.raises(OidcMetadataError):
        await cache.get(row, config)


@pytest.mark.asyncio
async def test_a_recovered_identity_provider_is_picked_up_again(idp: FakeIdp, clock: _Clock) -> None:
    """The backoff suppresses attempts, it does not latch the failure."""

    cache, row, config = get_oidc_metadata_cache(), _provider_row(), _config()
    await cache.get(row, config)
    idp.fail_metadata = True
    clock.advance(901)
    await cache.get(row, config)

    idp.fail_metadata = False
    clock.advance(61)
    assert (await cache.get(row, config)).issuer == ISSUER

    # Fetched successfully, so the next quarter of an hour is served from it.
    fetches = idp.metadata_fetches
    clock.advance(60)
    await cache.get(row, config)
    assert idp.metadata_fetches == fetches


@pytest.mark.asyncio
async def test_an_identity_provider_that_was_never_reachable_is_not_served(idp: FakeIdp, clock: _Clock) -> None:
    idp.fail_metadata = True
    with pytest.raises(OidcMetadataError):
        await get_oidc_metadata_cache().get(_provider_row(), _config())


# --- ID-token verification ------------------------------------------------


async def _metadata(idp: FakeIdp) -> Any:
    return await fetch_metadata(_config())


def _verify(metadata: Any, token: str, *, kid: str = "key-1", flow_created_at: int | None = None, **kwargs: Any) -> Any:
    now = int(time.time())
    return verify_id_token(
        token,
        config=_config(),
        metadata=metadata,
        key=metadata.key_for(kid),
        nonce_hash=NONCE_HASH,
        flow_created_at=now - 5 if flow_created_at is None else flow_created_at,
        now=kwargs.pop("now", now),
    )


@pytest.mark.asyncio
async def test_a_well_formed_token_verifies(idp: FakeIdp) -> None:
    metadata = await _metadata(idp)
    claims = _verify(metadata, idp.id_token(nonce=NONCE))
    assert claims["sub"] == "alice-subject"


@pytest.mark.asyncio
async def test_an_unsigned_token_is_refused(idp: FakeIdp) -> None:
    metadata = await _metadata(idp)
    with pytest.raises(OidcTokenError):
        _verify(metadata, idp.unsigned_token(nonce=NONCE))


@pytest.mark.asyncio
async def test_a_mac_algorithm_over_the_public_key_is_refused(idp: FakeIdp) -> None:
    """The classic confusion: an RSA public key used as an HMAC secret."""

    metadata = await _metadata(idp)
    with pytest.raises(OidcTokenError):
        _verify(metadata, idp.mac_token(nonce=NONCE))


@pytest.mark.asyncio
async def test_an_algorithm_outside_the_allow_list_is_refused(idp: FakeIdp) -> None:
    """The key declares RS256; a token signed with anything else does not verify against it."""

    signer = SigningKey.rsa("key-1", algorithm="RS512")
    idp.keys = [signer]
    idp.discovery_overrides["id_token_signing_alg_values_supported"] = ["RS256"]
    metadata = await _metadata(idp)
    with pytest.raises(OidcTokenError):
        _verify(metadata, idp.id_token(nonce=NONCE))


@pytest.mark.asyncio
async def test_an_elliptic_curve_key_verifies_on_its_own_curve(idp: FakeIdp) -> None:
    idp.keys = [SigningKey.ec("key-ec")]
    metadata = await _metadata(idp)
    assert _verify(metadata, idp.id_token(nonce=NONCE), kid="key-ec")["sub"] == "alice-subject"


@pytest.mark.asyncio
async def test_an_unknown_key_id_names_no_candidate(idp: FakeIdp) -> None:
    metadata = await _metadata(idp)
    assert metadata.key_for("never-published") is None


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-client"},
        {"iss": "https://evil.example.test"},
        {"aud": [CLIENT_ID, "another-client"]},
        {"aud": [CLIENT_ID, "another-client"], "azp": "another-client"},
        {"nonce": "some-other-nonce"},
        {"nonce": ABSENT},
        {"sub": ABSENT},
        {"iat": ABSENT},
    ],
    ids=[
        "wrong-aud",
        "wrong-iss",
        "multi-aud-no-azp",
        "multi-aud-wrong-azp",
        "wrong-nonce",
        "no-nonce",
        "no-sub",
        "no-iat",
    ],
)
@pytest.mark.asyncio
async def test_a_token_that_is_not_this_flow_s_is_refused(idp: FakeIdp, claims: dict[str, Any]) -> None:
    metadata = await _metadata(idp)
    with pytest.raises(OidcTokenError):
        _verify(metadata, idp.id_token(nonce=NONCE, claims=claims))


@pytest.mark.asyncio
async def test_several_audiences_with_a_matching_azp_are_accepted(idp: FakeIdp) -> None:
    metadata = await _metadata(idp)
    token = idp.id_token(nonce=NONCE, claims={"aud": [CLIENT_ID, "another-client"], "azp": CLIENT_ID})
    assert _verify(metadata, token)["sub"] == "alice-subject"


@pytest.mark.asyncio
async def test_an_expired_token_is_refused_once_it_is_past_the_skew(idp: FakeIdp) -> None:
    metadata = await _metadata(idp)
    now = int(time.time())

    just_expired = idp.id_token(nonce=NONCE, now=now - 330, lifetime=300)
    assert _verify(metadata, just_expired, flow_created_at=now - 400)["sub"] == "alice-subject"

    long_expired = idp.id_token(nonce=NONCE, now=now - 700, lifetime=300)
    with pytest.raises(OidcTokenError):
        _verify(metadata, long_expired, flow_created_at=now - 800)


@pytest.mark.asyncio
async def test_a_clock_a_little_ahead_still_signs_people_in(idp: FakeIdp) -> None:
    metadata = await _metadata(idp)
    now = int(time.time())

    slightly_ahead = idp.id_token(nonce=NONCE, now=now + 40)
    assert _verify(metadata, slightly_ahead, flow_created_at=now - 5)["sub"] == "alice-subject"

    far_ahead = idp.id_token(nonce=NONCE, now=now + 120)
    with pytest.raises(OidcTokenError):
        _verify(metadata, far_ahead, flow_created_at=now - 5)


@pytest.mark.asyncio
async def test_a_token_minted_before_the_flow_began_is_refused(idp: FakeIdp) -> None:
    """Independent of the skew: a captured token with a long expiry is still not this flow's."""

    metadata = await _metadata(idp)
    now = int(time.time())
    captured = idp.id_token(nonce=NONCE, now=now - 3600, lifetime=7200)

    with pytest.raises(OidcTokenError):
        _verify(metadata, captured, flow_created_at=now - 5)
    # ... and the same token is fine against the flow it actually answered.
    assert _verify(metadata, captured, flow_created_at=now - 3600 + OIDC_CLOCK_SKEW_SECONDS)["sub"]


# --- claims to identity ---------------------------------------------------


def test_two_spellings_of_a_subject_stay_two_identities() -> None:
    config = _config()
    upper = identity_from_claims({"sub": "Alice"}, config)
    lower = identity_from_claims({"sub": "alice"}, config)

    assert upper is not None and lower is not None
    assert upper.subject == "Alice" and lower.subject == "alice"
    assert upper.provider == "oidc" and upper.provider_key == "default"


def test_the_configured_claim_names_are_what_is_read() -> None:
    config = _config(groups_claim="roles", name_claim="preferred_username", email_claim="upn")
    identity = identity_from_claims(
        {
            "sub": "alice",
            "roles": ["Platform Admins", " engineers ", "Platform Admins"],
            "preferred_username": "Alice Smith",
            "upn": "Alice@Example.com",
            "groups": ["ignored"],
            "name": "ignored",
        },
        config,
    )

    assert identity is not None
    # Case-folded and de-duplicated, because that is what the stored rules match on.
    assert identity.groups == ("platform admins", "engineers")
    assert identity.display_name == "Alice Smith"
    assert identity.email == "alice@example.com"


def test_a_single_string_group_claim_is_one_group() -> None:
    identity = identity_from_claims({"sub": "alice", "groups": "Engineers"}, _config())
    assert identity is not None and identity.groups == ("engineers",)


def test_an_unverified_email_is_dropped() -> None:
    identity = identity_from_claims({"sub": "alice", "email": "alice@example.com", "email_verified": False}, _config())
    assert identity is not None and identity.email is None

    # Absent is not false: many identity providers omit the claim entirely.
    absent = identity_from_claims({"sub": "alice", "email": "alice@example.com"}, _config())
    assert absent is not None and absent.email == "alice@example.com"


@pytest.mark.parametrize(
    "verified",
    ["false", "False", "FALSE", 0, 1, "no", "yes", "", [], {}, None, "true ok"],
    ids=[
        "string-false",
        "capitalised-false",
        "upper-false",
        "zero",
        "one",
        "no",
        "yes",
        "empty-string",
        "empty-list",
        "empty-object",
        "null",
        "not-quite-true",
    ],
)
def test_only_an_affirmative_email_verified_keeps_the_address(verified: object) -> None:
    """Present and not affirmative is false, whatever shape it arrives in.

    A self-asserted address is what ``link_by_email`` links accounts by, what a
    new account is created with, and what ``email_domain`` role rules match on,
    so a claim this code cannot read as "yes" must not be read as one.
    """

    identity = identity_from_claims(
        {"sub": "alice", "email": "alice@example.com", "email_verified": verified}, _config()
    )
    assert identity is not None and identity.email is None


@pytest.mark.parametrize(
    "verified", [True, "true", "True", "TRUE", " true "], ids=["bool", "lower", "mixed", "upper", "padded"]
)
def test_the_string_spelling_of_a_verified_email_is_honoured(verified: object) -> None:
    """Google documents ``"true"``, Apple documents string-or-boolean, Cognito emits strings.

    Accepting only the JSON boolean would drop the address of every
    legitimately verified user at three of the identity providers this feature
    exists for.
    """

    identity = identity_from_claims(
        {"sub": "alice", "email": "alice@example.com", "email_verified": verified}, _config()
    )
    assert identity is not None and identity.email == "alice@example.com"


@pytest.mark.parametrize("subject", ["", "   ", "\t\n", "x" * 513, None, 42])
def test_a_subject_that_cannot_be_an_identity_is_refused(subject: object) -> None:
    assert identity_from_claims({"sub": subject}, _config()) is None


def test_a_subject_is_looked_up_exactly_as_the_identity_provider_issued_it() -> None:
    """Trimming is case-folding one character over.

    An identity provider free to issue ``alice`` may also issue ``<sp>alice``:
    they are two subjects, and a lookup that trims would sign the second person
    into the first one's account.
    """

    config = _config()
    padded = identity_from_claims({"sub": " alice "}, config)
    bare = identity_from_claims({"sub": "alice"}, config)

    assert padded is not None and bare is not None
    assert padded.subject == " alice " and bare.subject == "alice"
    assert padded.subject != bare.subject
    # Length is measured on what will be stored, so the column still bounds it.
    assert identity_from_claims({"sub": " " + "x" * 512}, config) is None


def test_the_display_name_is_bounded_by_its_column() -> None:
    identity = identity_from_claims({"sub": "alice", "name": "n" * 400}, _config())
    assert identity is not None and len(identity.display_name or "") == 128


def test_a_malformed_email_claim_is_simply_absent() -> None:
    identity = identity_from_claims({"sub": "alice", "email": "not-an-address"}, _config())
    assert identity is not None and identity.email is None


# --- the flow record ------------------------------------------------------


def test_a_flow_s_start_is_the_same_instant_on_either_backend() -> None:
    """SQLite returns a naive datetime and PostgreSQL an aware one; the ``iat``
    check reads the same instant from both, whatever the host's timezone is."""

    from datetime import timedelta, timezone

    from app.modules.dashboard_auth.oidc_flows import OidcFlowRecord

    naive_utc = datetime(2026, 9, 13, 12, 0, 0)
    aware_elsewhere = naive_utc.replace(tzinfo=UTC).astimezone(timezone(timedelta(hours=9)))

    def _record(created_at: datetime) -> OidcFlowRecord:
        return OidcFlowRecord(
            state_hash="hash",
            provider_id="provider-id",
            nonce_hash="hash",
            code_verifier="verifier",
            purpose="login",
            redirect_uri="https://dash.example.test/api/dashboard-auth/oidc/callback",
            config_fingerprint=oidc_config_fingerprint(_config()),
            created_at=created_at,
            expires_at=created_at,
        )

    assert _record(naive_utc).created_at_epoch == _record(aware_elsewhere).created_at_epoch
    assert _record(naive_utc).created_at_epoch == int(naive_utc.replace(tzinfo=UTC).timestamp())


# --- the provider's own surface -------------------------------------------


def test_the_provider_recognises_nobody_from_a_bare_request() -> None:
    bare = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    assert get_oidc_provider().resolve_identity(bare) is None


@pytest.mark.asyncio
async def test_the_authorization_request_is_always_s256(idp: FakeIdp) -> None:
    url = await get_oidc_provider().begin_login(
        provider=_provider_row(),
        config=_config(),
        state="state-value",
        nonce=NONCE,
        code_challenge="challenge-value",
    )

    assert "code_challenge_method=S256" in url
    assert "plain" not in url
    assert url.startswith("https://idp.example.test/authorize?")


@pytest.mark.asyncio
async def test_a_step_up_authorization_request_asks_for_a_fresh_sign_in(idp: FakeIdp) -> None:
    url = await get_oidc_provider().begin_login(
        provider=_provider_row(),
        config=_config(),
        state="state-value",
        nonce=NONCE,
        code_challenge="challenge-value",
        prompt_login=True,
    )

    assert "prompt=login" in url and "max_age=0" in url


@pytest.mark.asyncio
async def test_the_token_request_carries_the_verifier_and_never_the_state(idp: FakeIdp) -> None:
    idp.token_response = json_response({"id_token": idp.id_token(nonce=NONCE)})

    completion = await get_oidc_provider().complete_login(
        provider=_provider_row(),
        config=_config(),
        code="authorization-code",
        code_verifier="verifier-value",
        redirect_uri="https://dash.example.test/api/dashboard-auth/oidc/callback",
        nonce_hash=NONCE_HASH,
        flow_created_at=int(time.time()) - 5,
    )

    assert completion.identity.subject == "alice-subject"
    [exchange] = idp.token_requests()
    assert exchange.data["grant_type"] == "authorization_code"
    assert exchange.data["code_verifier"] == "verifier-value"
    assert exchange.data["redirect_uri"] == "https://dash.example.test/api/dashboard-auth/oidc/callback"
    assert "state" not in exchange.data
    assert exchange.allow_redirects is False


@pytest.mark.asyncio
async def test_a_refused_or_empty_token_response_ends_the_sign_in(idp: FakeIdp) -> None:
    from app.core.auth.providers.oidc import OidcExchangeError

    idp.token_response = json_response({"error": "invalid_grant", "error_description": "code already used"}, status=400)
    with pytest.raises(OidcExchangeError):
        await _complete(idp)

    idp.token_response = json_response({"access_token": "opaque"})
    with pytest.raises(OidcExchangeError):
        await _complete(idp)


async def _complete(idp: FakeIdp) -> Any:
    return await get_oidc_provider().complete_login(
        provider=_provider_row(),
        config=_config(),
        code="authorization-code",
        code_verifier="verifier-value",
        redirect_uri="https://dash.example.test/api/dashboard-auth/oidc/callback",
        nonce_hash=NONCE_HASH,
        flow_created_at=int(time.time()) - 5,
    )


@pytest.mark.asyncio
async def test_a_token_response_delivered_in_pieces_does_not_burn_the_code(idp: FakeIdp) -> None:
    """The authorization code is spent by the request, so a short read loses a valid sign-in.

    The flow row is consumed before the exchange and the code cannot be
    presented twice, so a token response read as a truncated prefix is not a
    retry away from working: that person's sign-in is simply over.
    """

    idp.token_response = json_response({"id_token": idp.id_token(nonce=NONCE)}, chunk_size=8)

    completion = await _complete(idp)

    assert completion.identity.subject == "alice-subject"


@pytest.mark.asyncio
async def test_an_unknown_key_id_forces_one_refresh_and_then_refuses(idp: FakeIdp, clock: _Clock) -> None:
    rotated = SigningKey.rsa("key-2")
    idp.token_response = json_response({"id_token": idp.id_token(nonce=NONCE, key=rotated)})
    idp.keys.append(rotated)

    # The provider has never fetched, so the first call already has the key.
    assert (await _complete(idp)).identity.subject == "alice-subject"

    # A token signed by a key the identity provider never published: one forced
    # refresh, then a refusal rather than an unverified acceptance.
    never_published = SigningKey.rsa("key-3")
    idp.token_response = json_response({"id_token": idp.id_token(nonce=NONCE, key=never_published)})
    fetches_before = idp.metadata_fetches
    with pytest.raises(OidcTokenError):
        await _complete(idp)
    assert idp.metadata_fetches == fetches_before + 1


@pytest.mark.asyncio
async def test_the_client_secret_travels_in_the_body_not_the_url(idp: FakeIdp) -> None:
    idp.token_response = json_response({"id_token": idp.id_token(nonce=NONCE)})
    await _complete(idp)

    [exchange] = idp.token_requests()
    assert "client_secret" in exchange.data
    assert "client_secret" not in exchange.url and "?" not in exchange.url


@pytest.mark.asyncio
async def test_basic_authentication_is_used_when_the_identity_provider_asks_for_it(idp: FakeIdp) -> None:
    idp.discovery_overrides["token_endpoint_auth_methods_supported"] = ["client_secret_basic"]
    idp.token_response = json_response({"id_token": idp.id_token(nonce=NONCE)})
    await _complete(idp)

    [exchange] = idp.token_requests()
    assert exchange.headers["Authorization"].startswith("Basic ")
    assert "client_secret" not in exchange.data

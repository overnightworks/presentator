"""The relying party this instance signs people in through, built from settings."""

import time
from typing import Final

from pydantic import SecretStr
from webauth.https_client import fetch_key_set_over_https
from webauth.oidc import OidcClientConfig, OidcRelyingParty
from webauth.oidc_login import AdmissionConfig
from webauth.oidc_transport import post_token_request_over_https

from presentator.api.auth import LOGIN_PATH, SIGN_IN_CALLBACK_PATH
from presentator.host.config import Settings, cannot_start, read_secret_file

_PROTOCOL_PATH: Final = "protocol/openid-connect"
_SCOPES: Final = ("openid", "profile", "groups")
_CLOCK_LEEWAY_SECONDS: Final = 30
# Who may enter is set in Keycloak alone: these two group paths are the
# product's, not a deployment's.
_LOGIN_GROUP: Final = "/apps/presentator"
_ADMIN_GROUP: Final = "/apps/presentator/admin"


def oidc_client_config(
    settings: Settings, client_secret: SecretStr, *, origin: str
) -> OidcClientConfig:
    """The Keycloak client these settings name, returning to `origin`.

    The lobby and the talk host sign in through the one client, each as its own
    relying party with its own redirect URI; signing out returns to the lobby.
    """
    realm = f"{settings.oidc_issuer}/{_PROTOCOL_PATH}"
    try:
        return OidcClientConfig(
            issuer=settings.oidc_issuer,
            authorization_endpoint=f"{realm}/auth",
            token_endpoint=f"{realm}/token",
            jwks_url=f"{realm}/certs",
            end_session_endpoint=f"{realm}/logout",
            client_id=settings.oidc_client_id,
            client_secret=client_secret,
            redirect_uri=f"{origin}{SIGN_IN_CALLBACK_PATH}",
            post_logout_redirect_uri=f"{settings.public_origin}{LOGIN_PATH}",
            scopes=_SCOPES,
            leeway_seconds=_CLOCK_LEEWAY_SECONDS,
        )
    except ValueError as refused:
        # webauth's reasons name a field, never its value.
        message = f"the sign-in client configuration: {refused}"
        raise cannot_start(message) from None


def relying_party(config: OidcClientConfig) -> OidcRelyingParty:
    """The relying party over the library's production transports and clocks."""
    return OidcRelyingParty(
        config,
        post_token_request=post_token_request_over_https,
        fetch_key_set=fetch_key_set_over_https,
        wall_clock=time.time,
        monotonic_clock=time.monotonic,
    )


def client_configs_of(settings: Settings) -> tuple[OidcClientConfig, OidcClientConfig]:
    """The lobby's client and the talk host's, from the one secret file read once."""
    secret = read_secret_file(
        settings.oidc_client_secret_file, setting="oidc_client_secret_file"
    )
    return (
        oidc_client_config(settings, secret, origin=settings.public_origin),
        oidc_client_config(settings, secret, origin=settings.talk_origin),
    )


def admission_of(settings: Settings) -> AdmissionConfig:
    """Only members of the app's Keycloak group enter, and its admin group admins."""
    return AdmissionConfig(
        bound_issuer=settings.oidc_issuer,
        login_group=_LOGIN_GROUP,
        admin_group=_ADMIN_GROUP,
    )

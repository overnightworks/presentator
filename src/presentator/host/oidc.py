"""The sign-in client and admission this instance runs, built from settings."""

from typing import Final

from pydantic import SecretStr
from webauth.admission import AdmissionConfig
from webauth.oidc import OidcClientConfig

from presentator.api.auth import LOGIN_PATH, SIGN_IN_CALLBACK_PATH
from presentator.host.config import Settings, read_secret_file
from presentator.host.environment import REALM_PREFIX, cannot_start, variable_of

_PROTOCOL_PATH: Final = "protocol/openid-connect"
_PERSON_SCOPES: Final = ("openid", "profile", "groups")
_CLOCK_LEEWAY_SECONDS: Final = 30
# The variables a client configuration is built from; the lobby's and the talk
# host's clients differ only in their origin.
_CLIENT_VARIABLES: Final = (
    variable_of("oidc_issuer"),
    variable_of("client_id", prefix=REALM_PREFIX),
    variable_of("oidc_client_secret_file"),
    variable_of("speech_scope", prefix=REALM_PREFIX),
    variable_of("public_origin"),
    variable_of("talk_origin"),
)


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
            client_id=settings.realm.client_id,
            client_secret=client_secret,
            redirect_uri=f"{origin}{SIGN_IN_CALLBACK_PATH}",
            post_logout_redirect_uri=f"{settings.public_origin}{LOGIN_PATH}",
            scopes=(*_PERSON_SCOPES, settings.realm.speech_scope),
            leeway_seconds=_CLOCK_LEEWAY_SECONDS,
        )
    except ValueError as refused:
        # webauth's reasons name a field, never its value.
        message = f"the sign-in client configuration: {refused}"
        raise cannot_start(message, variables=_CLIENT_VARIABLES) from None


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
        login_group=settings.realm.login_group,
        admin_group=settings.realm.admin_group,
    )

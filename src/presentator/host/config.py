"""What varies by deployment; everything else is a constant beside its owner."""

import re
import threading
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Annotated, Final, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from webauth.proxies import TrustedProxies

from presentator.adapters.build_wire import SOCKET_DIRECTORY, SOCKET_NAME

# Every secret this instance is handed is at least this many characters, so no
# guard of it rests on a value somebody could type.
SECRET_LENGTH: Final = 32

_INVALID_PORT: Final = "has an invalid port"
_REFUSED: Final = "this environment cannot start an instance"
_EMPTY_CLIENT_ID: Final = "token_clients: names an empty client id"
_ONE_SCOPE_TOKEN: Final = (
    "is one scope token of printable characters without a space, quote or backslash"
)
_SCOPE_TOKEN: Final = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]+")
_ONE_HOSTNAME: Final = "talk_origin: is another hostname than public_origin"

ENVIRONMENT_PREFIX: Final = "PRESENTATOR_"
# What the web and the build worker both bound one build step by.
DEFAULT_BUILD_TIMEOUT_SECONDS: Final = 300.0
# What the web and the build worker both bound one build's output by.
DEFAULT_BUILD_OUTPUT_MEGABYTES: Final = 300
# The watchdog cannot be armed for longer than threading.TIMEOUT_MAX, and the
# worker's fixed phases and the clock offset come on top of the build step.
LONGEST_BUILD_TIMEOUT_SECONDS: Final = threading.TIMEOUT_MAX / 2


class WhereBuildsRun(StrEnum):
    """Where the toolchain that builds a deck runs (line 14a).

    The worker is the sealed build worker service, reached over its socket and
    what an instance is; this machine itself is a development run, and a
    deployment gets it only by saying so.
    """

    HOST = "host"
    WORKER = "worker"


class ConfigurationError(ValueError):
    """What the environment carries is not a configuration to run on."""


def _a_proxy_list(given: str) -> str:
    """Refuse a list that is not addresses or networks."""
    TrustedProxies.parse(given)
    return given


def _client_ids(given: str) -> str:
    """Refuse a list with an empty entry; an empty list admits no client."""
    if given and "" in _split_client_ids(given):
        raise ValueError(_EMPTY_CLIENT_ID)
    return given


def _split_client_ids(given: str) -> list[str]:
    return [entry.strip() for entry in given.split(",")]


def _one_public_origin(given: str) -> str:
    """Require one canonical https origin, without any address suffix."""
    return _one_origin(given, schemes=("https",))


def _one_service_origin(given: str) -> str:
    """Require one canonical http or https origin, as the network inside gives."""
    return _one_origin(given, schemes=("http", "https"))


def _one_origin(given: str, *, schemes: tuple[str, ...]) -> str:
    parsed = urlsplit(given)
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or given != f"{parsed.scheme}://{parsed.netloc}"
    ):
        message = (
            f"is one canonical {' or '.join(schemes)} origin"
            " without credentials or a path"
        )
        raise ValueError(message)
    try:
        _port = parsed.port
    except ValueError as refused:
        message = _INVALID_PORT
        raise ValueError(message) from refused
    return given


def _one_scope_token(given: str) -> str:
    """Require one RFC 6749 scope-token: 1*( %x21 / %x23-5B / %x5D-7E )."""
    if not _SCOPE_TOKEN.fullmatch(given):
        raise ValueError(_ONE_SCOPE_TOKEN)
    return given


def _an_https_realm_url(given: str) -> str:
    """Require one canonical https URL to a realm, which ends in no slash."""
    parsed = urlsplit(given)
    if (
        not given.startswith("https://")
        or not parsed.hostname
        or parsed.netloc != parsed.netloc.lower()
        or any(
            character.isspace() or not character.isprintable() for character in given
        )
        or parsed.username is not None
        or parsed.password is not None
        or "?" in given
        or "#" in given
        or given.endswith("/")
    ):
        message = (
            "is a lower-case https URL without credentials, query or trailing slash"
        )
        raise ValueError(message)
    if parsed.netloc.endswith(":"):
        message = _INVALID_PORT
        raise ValueError(message)
    try:
        _port = parsed.port
    except ValueError as refused:
        message = _INVALID_PORT
        raise ValueError(message) from refused
    return given


def _an_absolute_path(given: Path) -> Path:
    if not given.is_absolute():
        message = "is an absolute path"
        raise ValueError(message)
    return given


class Settings(BaseSettings, env_prefix=ENVIRONMENT_PREFIX, env_file=".env"):
    """Read from `PRESENTATOR_*` in the environment, or from a local `.env`."""

    # Where the master key is read from once at start, so it never rides the
    # environment.
    secret_key_file: Annotated[Path, AfterValidator(_an_absolute_path)]
    database: Path = Path("presentator.sqlite3")
    mirrors: Path = Path("mirrors")
    # The one directory a file-kind source's address may resolve under; the
    # image fixes it to the path a host directory is bound to, and a direct
    # run keeps it beside the checkout like every other path above.
    local_sources_mount: Path = Path("local-sources")
    # Where built talks are kept, and the Node project whose Slidev builds
    # them; both are places on the machine the instance runs on.
    builds: Path = Path("builds")
    toolchain: Path = Path("slidev")
    # A build that hangs would hold every later build behind it, so each step
    # of the toolchain is bounded.
    build_timeout_seconds: float = DEFAULT_BUILD_TIMEOUT_SECONDS
    # A deck is code, so every build runs in the sealed build worker. Running
    # the toolchain on this machine instead is a development run, and it takes
    # saying so.
    build_runner: WhereBuildsRun = WhereBuildsRun.WORKER
    # Where the build worker listens; the default is where it binds.
    build_worker_socket: Annotated[Path, AfterValidator(_an_absolute_path)] = (
        Path(SOCKET_DIRECTORY) / SOCKET_NAME
    )
    # What one build may leave behind, in whole megabytes for its talk.
    build_output_megabytes: int = DEFAULT_BUILD_OUTPUT_MEGABYTES
    # Polling is what makes a push arrive at all, so it runs whether or not any
    # host ever calls the hook.
    source_poll_seconds: float = 300.0
    # A pull that hangs would hold the tick it runs on, so it is bounded.
    source_timeout_seconds: float = 20.0
    host: str = "127.0.0.1"
    port: int = 8000
    public_origin: Annotated[str, AfterValidator(_one_public_origin)]
    # The talk host is an origin of its own, so a deck's code runs on a site the
    # lobby's cookies never reach. It is a deployment fact: no default.
    talk_origin: Annotated[str, AfterValidator(_one_public_origin)]
    # The issuer is the realm the lobby signs people in at; the secret is read
    # from a file once at start, so it never rides the environment.
    oidc_issuer: Annotated[str, AfterValidator(_an_https_realm_url)]
    oidc_client_id: str = Field(min_length=1)
    oidc_client_secret_file: Annotated[Path, AfterValidator(_an_absolute_path)]
    # Where the agent service answers: its origin on the network it shares with
    # this instance. It is a deployment fact: no default.
    agent_service: Annotated[str, AfterValidator(_one_service_origin)]
    # The scope the workload grant asks for, so the token it gets names the agent
    # service as its audience. It is a deployment fact: no default.
    agent_service_scope: Annotated[str, AfterValidator(_one_scope_token)]
    speech_socket: Annotated[Path, AfterValidator(_an_absolute_path)] = Path(
        "/run/presentator-speech/speech.sock"
    )
    # Empty: the client address is the ASGI peer. A list is the peers whose
    # X-Forwarded-For this instance believes.
    trusted_proxies: Annotated[str, AfterValidator(_a_proxy_list)] = ""
    # The `azp` of the access tokens `/api/` takes, hal's client ids: a token
    # opens `/api/` as the person and `/api/admin/` for an admin. Empty: no
    # token is admitted and the admin routes do not exist.
    token_clients: Annotated[str, AfterValidator(_client_ids)] = ""

    @cached_property
    def secret_key(self) -> SecretStr:
        """The master key, read once from its file."""
        return read_secret_file(
            self.secret_key_file,
            setting="secret_key_file",
            minimum_length=SECRET_LENGTH,
        )

    def admitted_token_clients(self) -> frozenset[str]:
        """The client ids whose tokens `/api/` takes."""
        if not self.token_clients:
            return frozenset()
        return frozenset(_split_client_ids(self.token_clients))

    @model_validator(mode="after")
    def _two_hostnames(self) -> Self:
        # The cookies of the two origins stay apart only on two hostnames.
        if urlsplit(self.talk_origin).hostname == urlsplit(self.public_origin).hostname:
            raise ValueError(_ONE_HOSTNAME)
        return self


class WorkerSettings(BaseSettings):
    """What the build worker reads of its container's environment, and no file."""

    model_config = SettingsConfigDict(env_prefix=ENVIRONMENT_PREFIX, frozen=True)

    build_timeout_seconds: float = Field(
        default=DEFAULT_BUILD_TIMEOUT_SECONDS,
        gt=0,
        le=LONGEST_BUILD_TIMEOUT_SECONDS,
        allow_inf_nan=False,
    )
    build_output_megabytes: int = Field(default=DEFAULT_BUILD_OUTPUT_MEGABYTES, gt=0)


def _variable_of(field_name: str) -> str:
    return f"{ENVIRONMENT_PREFIX}{field_name}".upper()


def worker_settings_variables() -> frozenset[str]:
    """The environment variables the worker reads, derived from its fields."""
    return frozenset(_variable_of(name) for name in WorkerSettings.model_fields)


def load_worker_settings() -> WorkerSettings:
    """Read the worker's settings, refusing a value its limits cannot hold."""
    try:
        return WorkerSettings.model_validate({})
    except ValidationError as refused:
        # `msg` states the rule that failed and never quotes the value.
        raise cannot_start(
            ", ".join(
                f"{_variable_of(str(fault['loc'][0]))}: {fault['msg']}"
                for fault in refused.errors()
            )
        ) from None


def read_secret_file(
    secret_file: Path, *, setting: str, minimum_length: int = 1
) -> SecretStr:
    """Read a secret once, minus the one newline an editor leaves.

    The refusals name the setting and never what the file holds or what the
    operating system says of it, which may quote either.
    """
    try:
        content = secret_file.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        unreadable = f"{setting}: is a file this instance can read"
        raise cannot_start(unreadable) from None
    secret = content.removesuffix("\n")
    if not secret.strip():
        empty = f"{setting}: is a file that holds the secret"
        raise cannot_start(empty)
    if len(secret) < minimum_length:
        short = f"{setting}: is a file that holds at least {minimum_length} characters"
        raise cannot_start(short)
    return SecretStr(secret)


def cannot_start(reason: str) -> ConfigurationError:
    """The refusal an environment no instance can run on is answered with."""
    return ConfigurationError(f"{_REFUSED}: {reason}")


def load_settings() -> Settings:
    """Read what the environment carries, refusing what cannot start an instance.

    pydantic-settings fills the fields while it validates, so this call is what
    reads `PRESENTATOR_*`; the constructor would ask for the required key as an
    argument instead of looking for it.
    """
    try:
        settings = Settings.model_validate({})
    except ValidationError as refused:
        # A refused value is often a real secret, and the library's own message
        # quotes it; only the field and the reason may leave this call.
        raise cannot_start(_what_is_wrong(refused)) from None
    # Reading the key here refuses a bad key file at start, after the other
    # settings validated, and binds it for every later use.
    _ = settings.secret_key
    return settings


def _what_is_wrong(refused: ValidationError) -> str:
    return ", ".join(
        # A fault of the whole model has no field; its reason already names one.
        str(fault.get("ctx", {}).get("error", fault["msg"]))
        if not fault["loc"]
        else f"{'.'.join(str(part) for part in fault['loc'])}: {fault['msg']}"
        for fault in refused.errors()
    )

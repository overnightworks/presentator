"""What varies by deployment; everything else is a constant beside its owner."""

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
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

from presentator.adapters.speech import VoiceId, voice_speaks
from presentator.agent.settings import AgentSettings
from presentator.contracts.voice import SampleLanguage

# Every secret this instance is handed is at least this many characters, so no
# guard of it rests on a value somebody could type.
SECRET_LENGTH: Final = 32

_INVALID_PORT: Final = "has an invalid port"
_SCOPE_TOKEN: Final = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]+")
_REFUSED: Final = "this environment cannot start an instance"
_EMPTY_CLIENT_ID: Final = "token_clients: names an empty client id"
_VOICE_SPEAKS_OTHER: Final = "names a voice that does not speak it"
_ONE_HOSTNAME: Final = "is another hostname than public_origin"
_WORKER_REACHED: Final = (
    "is set together with build_worker_key_file, and both are set when builds"
    " run in the worker"
)
_DATABASE_NAMED_WHOLE: Final = (
    "is set together with database_password_file, or neither is"
)

ENVIRONMENT_PREFIX: Final = "PRESENTATOR_"
_NESTED: Final = "__"
# The shortest key of the build worker's hello, the web's file and the worker's alike.
MINIMUM_KEY_LENGTH: Final = 32
# What the web and the build worker both bound one build step by.
DEFAULT_BUILD_TIMEOUT_SECONDS: Final = 300.0
# What the web and the build worker both bound one build's output by.
DEFAULT_BUILD_OUTPUT_MEGABYTES: Final = 300
# The watchdog cannot be armed for longer than threading.TIMEOUT_MAX, and the
# worker's fixed phases and the clock offset come on top of the build step.
LONGEST_BUILD_TIMEOUT_SECONDS: Final = threading.TIMEOUT_MAX / 2


class WhereBuildsRun(StrEnum):
    """Where the toolchain that builds a deck runs (line 14a).

    The worker is the sealed build worker service, reached over TCP and what an
    instance is; this machine itself is a development run, and a
    deployment gets it only by saying so.
    """

    HOST = "host"
    WORKER = "worker"


class _FieldRefusedError(ValueError):
    """A rule over several settings refused them; `field` is the one it names."""

    def __init__(self, rule: str, *, field: str) -> None:
        """Refuse `field` by `rule`."""
        super().__init__(f"{field}: {rule}")
        self.field = field


class ConfigurationError(ValueError):
    """What the environment carries is not a configuration to run on.

    `variables` names the environment variables that were refused, when it is
    known which; they are setting names, never values.
    """

    def __init__(self, message: str, *, variables: tuple[str, ...] = ()) -> None:
        """Say why, and which variables."""
        super().__init__(message)
        self.variables = variables


def worker_endpoint(address: str) -> tuple[str, int]:
    """The host and port of a `host:port` address; refuse any other form."""
    parsed = urlsplit(f"//{address}")
    try:
        port = parsed.port
    except ValueError as refused:
        message = _INVALID_PORT
        raise ValueError(message) from refused
    if not parsed.hostname or not port or parsed.netloc != address or "@" in address:
        message = "is a host and a port, as host:port"
        raise ValueError(message)
    return parsed.hostname, port


def _a_worker_address(given: str) -> str:
    worker_endpoint(given)
    return given


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


def _one_http_origin(given: str) -> str:
    """Require one canonical http origin; the speech client refuses any other."""
    return _one_origin(given, schemes=("http",))


def _one_scope_token(given: str) -> str:
    """Require one RFC 6749 scope token, so one grant asks for exactly one scope."""
    if _SCOPE_TOKEN.fullmatch(given) is None:
        message = "is one scope token, without spaces, quotes or backslashes"
        raise ValueError(message)
    return given


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


def _a_postgres_url_without_a_password(given: str) -> str:
    """Require a postgresql URL of a host, a user and a database, and no password.

    The password lives in a file, and libpq lets a query parameter redirect it.
    """
    try:
        parsed = urlsplit(given)
        _port = parsed.port
    except ValueError:
        # The parser's message quotes the URL.
        message = "is a postgresql URL"
        raise ValueError(message) from None
    if parsed.scheme != "postgresql" or not (
        parsed.hostname and parsed.username and parsed.path.removeprefix("/")
    ):
        message = "is a postgresql URL naming a host, a user and a database"
        raise ValueError(message)
    if parsed.password is not None or parsed.query or parsed.fragment:
        message = "is a postgresql URL without a password, query or fragment"
        raise ValueError(message)
    return given


_REDIS_DATABASE_PATH: Final = re.compile(r"(/\d+)?", re.ASCII)


def _a_redis_url(given: SecretStr) -> SecretStr:
    """Require a redis URL of a host and at most a database number.

    The URL may carry the password, so it is a secret and no message quotes it;
    the client reads its own options from a query, so none is allowed.
    """
    try:
        parsed = urlsplit(given.get_secret_value())
        _port = parsed.port
    except ValueError:
        # The parser's message quotes the URL.
        message = "is a redis URL"
        raise ValueError(message) from None
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        message = "is a redis or rediss URL naming a host"
        raise ValueError(message)
    if (
        parsed.query
        or parsed.fragment
        or _REDIS_DATABASE_PATH.fullmatch(parsed.path) is None
    ):
        message = (
            "is a redis URL without a query or fragment,"
            " ending in at most a database number"
        )
        raise ValueError(message)
    return given


def _an_absolute_path(given: Path) -> Path:
    if not given.is_absolute():
        message = "is an absolute path"
        raise ValueError(message)
    return given


type PostgresUrl = Annotated[str, AfterValidator(_a_postgres_url_without_a_password)]
type RedisUrl = Annotated[SecretStr, AfterValidator(_a_redis_url)]
type AbsolutePath = Annotated[Path, AfterValidator(_an_absolute_path)]


@dataclass(frozen=True, slots=True)
class BuildWorkerLink:
    """Where the build workers answer and the key that proves this instance to them."""

    host: str
    port: int
    key: SecretStr


@dataclass(frozen=True, slots=True)
class PostgresDatabase:
    """Where Postgres is and the password it is reached with."""

    url: str
    password: SecretStr


def _postgres_database(url: str, password_file: Path) -> PostgresDatabase:
    return PostgresDatabase(
        url=url,
        password=read_secret_file(password_file, setting="database_password_file"),
    )


class Settings(
    BaseSettings,
    env_prefix=ENVIRONMENT_PREFIX,
    env_file=".env",
    env_nested_delimiter=_NESTED,
):
    """Read from `PRESENTATOR_*` in the environment, or from a local `.env`."""

    # Where the master key is read from once at start, so it never rides the
    # environment.
    secret_key_file: Annotated[Path, AfterValidator(_an_absolute_path)]
    database: Path = Path("presentator.sqlite3")
    # Setting the URL is what makes Postgres the database: every store is then
    # served from it and the SQLite file above is not opened. The password
    # lives in a file, read once at start, so it never rides the environment.
    database_url: PostgresUrl | None = None
    database_password_file: AbsolutePath | None = None
    # The Redis every pod shares for its limits and its ask lease; it keeps
    # nothing durable. A password may ride the URL, which is why it is a secret,
    # or live in a file, read once at start, like the database's. A
    # deployment fact: no default.
    redis_url: RedisUrl = Field(repr=False)
    redis_password_file: AbsolutePath | None = None
    mirrors: Path = Path("mirrors")
    # Where built talks are kept, and the Node project whose Slidev builds
    # them; both are places on the machine the instance runs on.
    builds: Path = Path("builds")
    toolchain: Path = Path("slidev")
    # Where the lobby app's build lies; a direct run serves the checkout's
    # `pnpm build` output, and the image fixes it to the copy it carries.
    lobby_build: Path = Path("frontend/build")
    # A build that hangs would hold every later build behind it, so each step
    # of the toolchain is bounded.
    build_timeout_seconds: float = DEFAULT_BUILD_TIMEOUT_SECONDS
    # A deck is code, so every build runs in the sealed build worker. Running
    # the toolchain on this machine instead is a development run, and it takes
    # saying so.
    build_runner: WhereBuildsRun = WhereBuildsRun.WORKER
    # Where the build workers answer, and the file holding the key that proves
    # this instance to them; the instance starts without a worker answering.
    build_worker_address: Annotated[str, AfterValidator(_a_worker_address)] | None = (
        None
    )
    build_worker_key_file: AbsolutePath | None = None
    # What one build may leave behind, in whole megabytes for its talk.
    build_output_megabytes: int = DEFAULT_BUILD_OUTPUT_MEGABYTES
    # Polling is what makes a push arrive at all, so it runs whether or not any
    # host ever calls the hook.
    source_poll_seconds: float = 300.0
    # A pull that hangs would hold the tick it runs on, so it is bounded; a
    # full-history pull of a large public repository takes tens of seconds, so
    # the bound leaves room for it.
    source_timeout_seconds: float = 180.0
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
    # Which backend answers, where its gateway is, the file holding this app's
    # key there and the group that may ask. They are deployment facts: no default.
    agent: AgentSettings
    # Where the shared speech service answers and the scope that names it at the
    # issuer are deployment facts: no default. The client reaches only a plain
    # http origin on internal networks, so this is the one origin here that is
    # not https.
    speech_service: Annotated[str, AfterValidator(_one_http_origin)]
    speech_service_scope: Annotated[str, AfterValidator(_one_scope_token)]
    # The voice an answer is spoken in, one per language, each a catalogue voice
    # that speaks that language.
    speech_voice_de: VoiceId = VoiceId.THORSTEN
    speech_voice_en: VoiceId = VoiceId.NORMAN
    # Empty: the client address is the ASGI peer. A list is the peers whose
    # X-Forwarded-For this instance believes.
    trusted_proxies: Annotated[str, AfterValidator(_a_proxy_list)] = ""
    # The `azp` of the access tokens `/api/` takes, hal's client ids: a token
    # opens `/api/` as the person and `/api/admin/` for an admin. Empty: no
    # token client is admitted and the admin routes do not exist; the agents
    # client of `/mcp` is no token client and never opens them.
    token_clients: Annotated[str, AfterValidator(_client_ids)] = ""

    @cached_property
    def secret_key(self) -> SecretStr:
        """The master key, read once from its file."""
        return read_secret_file(
            self.secret_key_file,
            setting="secret_key_file",
            minimum_length=SECRET_LENGTH,
        )

    @cached_property
    def build_worker(self) -> BuildWorkerLink:
        """The build workers' address and the key read once from its file."""
        if self.build_worker_address is None or self.build_worker_key_file is None:
            field = "build_worker_address"
            unreached = f"{field}: {_WORKER_REACHED}"
            raise cannot_start(unreached, variables=(variable_of(field),))
        host, port = worker_endpoint(self.build_worker_address)
        return BuildWorkerLink(
            host=host,
            port=port,
            key=read_secret_file(
                self.build_worker_key_file,
                setting="build_worker_key_file",
                minimum_length=MINIMUM_KEY_LENGTH,
            ),
        )

    @cached_property
    def postgres(self) -> PostgresDatabase | None:
        """The Postgres this instance serves from; nothing means the SQLite file."""
        if self.database_url is None or self.database_password_file is None:
            return None
        return _postgres_database(self.database_url, self.database_password_file)

    @cached_property
    def redis_password(self) -> SecretStr | None:
        """The password of the shared Redis, read from its file; none when unset."""
        if self.redis_password_file is None:
            return None
        return read_secret_file(self.redis_password_file, setting="redis_password_file")

    def admitted_token_clients(self) -> frozenset[str]:
        """The client ids whose tokens `/api/` takes."""
        if not self.token_clients:
            return frozenset()
        return frozenset(_split_client_ids(self.token_clients))

    def speech_voices(self) -> Mapping[SampleLanguage, VoiceId]:
        """The voice an answer is spoken in, by the language of the answer."""
        return {
            SampleLanguage.GERMAN: self.speech_voice_de,
            SampleLanguage.ENGLISH: self.speech_voice_en,
        }

    @model_validator(mode="after")
    def _voices_speak_their_language(self) -> Self:
        for language, voice in self.speech_voices().items():
            if not voice_speaks(voice, language):
                field = f"speech_voice_{language.value}"
                raise _FieldRefusedError(_VOICE_SPEAKS_OTHER, field=field)
        return self

    @model_validator(mode="after")
    def _database_is_named_whole(self) -> Self:
        if (self.database_url is None) != (self.database_password_file is None):
            raise _FieldRefusedError(_DATABASE_NAMED_WHOLE, field="database_url")
        return self

    @model_validator(mode="after")
    def _worker_is_reached(self) -> Self:
        named = (self.build_worker_address is None) == (
            self.build_worker_key_file is None
        )
        if not named or (
            self.build_runner is WhereBuildsRun.WORKER
            and self.build_worker_address is None
        ):
            raise _FieldRefusedError(_WORKER_REACHED, field="build_worker_address")
        return self

    @model_validator(mode="after")
    def _two_hostnames(self) -> Self:
        # The cookies of the two origins stay apart only on two hostnames.
        if urlsplit(self.talk_origin).hostname == urlsplit(self.public_origin).hostname:
            raise _FieldRefusedError(_ONE_HOSTNAME, field="talk_origin")
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


class DatabaseSettings(BaseSettings):
    """What `presentator migrate` reads, so it needs no other setting."""

    model_config = SettingsConfigDict(
        env_prefix=ENVIRONMENT_PREFIX, hide_input_in_errors=True
    )

    # The URL names no password: the file is its one owner, and it is read once
    # at start, so it never rides the environment.
    database_url: PostgresUrl
    database_password_file: AbsolutePath

    @cached_property
    def postgres(self) -> PostgresDatabase:
        """The database, with its password read once from its file."""
        return _postgres_database(self.database_url, self.database_password_file)


def variable_of(field_name: str) -> str:
    """The environment variable that carries a settings field."""
    return f"{ENVIRONMENT_PREFIX}{field_name}".upper()


def worker_settings_variables() -> frozenset[str]:
    """The environment variables the worker reads, derived from its fields."""
    return frozenset(variable_of(name) for name in WorkerSettings.model_fields)


def load_worker_settings() -> WorkerSettings:
    """Read the worker's settings, refusing a value its limits cannot hold."""
    try:
        return WorkerSettings.model_validate({})
    except ValidationError as refused:
        # `msg` states the rule that failed and never quotes the value.
        faults = [
            (variable_of(str(fault["loc"][0])), fault["msg"])
            for fault in refused.errors()
        ]
        raise cannot_start(
            ", ".join(f"{variable}: {rule}" for variable, rule in faults),
            variables=tuple(variable for variable, _ in faults),
        ) from None


def read_secret_file(
    secret_file: Path, *, setting: str, minimum_length: int = 1
) -> SecretStr:
    """Read a secret once, minus the one newline an editor leaves.

    The refusals name the setting and never what the file holds or what the
    operating system says of it, which may quote either.
    """
    variables = (variable_of(setting),)
    try:
        content = secret_file.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        unreadable = f"{setting}: is a file this instance can read"
        raise cannot_start(unreadable, variables=variables) from None
    secret = content.removesuffix("\n")
    if not secret.strip():
        empty = f"{setting}: is a file that holds the secret"
        raise cannot_start(empty, variables=variables)
    if len(secret) < minimum_length:
        short = f"{setting}: is a file that holds at least {minimum_length} characters"
        raise cannot_start(short, variables=variables)
    return SecretStr(secret)


def cannot_start(reason: str, *, variables: tuple[str, ...] = ()) -> ConfigurationError:
    """The refusal an environment no instance can run on is answered with."""
    return ConfigurationError(f"{_REFUSED}: {reason}", variables=variables)


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
        raise _refused_by(refused) from None
    # Reading the key here refuses a bad key file at start, after the other
    # settings validated, and binds it for every later use.
    _ = settings.secret_key
    _ = settings.postgres
    return settings


def load_database_settings() -> DatabaseSettings:
    """Read what the environment carries for the database, refusing what is wrong."""
    try:
        settings = DatabaseSettings.model_validate({})
    except ValidationError as refused:
        raise _refused_by(refused) from None
    _ = settings.postgres
    return settings


def _refused_by(refused: ValidationError) -> ConfigurationError:
    """The refusal of a validation, naming the variables of the fields it refused."""
    fields: list[str] = []
    for fault in refused.errors():
        # A rule over several fields has no location; its error names the field.
        error = fault.get("ctx", {}).get("error")
        if isinstance(error, _FieldRefusedError):
            fields.append(error.field)
        else:
            # A nested setting's variable joins its path as the environment does.
            fields.append(_NESTED.join(str(part) for part in fault["loc"]))
    variables = tuple(variable_of(field) for field in dict.fromkeys(fields))
    return cannot_start(_what_is_wrong(refused), variables=variables)


def _what_is_wrong(refused: ValidationError) -> str:
    return ", ".join(
        # A fault of the whole model has no field; its reason already names one.
        str(fault.get("ctx", {}).get("error", fault["msg"]))
        if not fault["loc"]
        else f"{'.'.join(str(part) for part in fault['loc'])}: {fault['msg']}"
        for fault in refused.errors()
    )

"""What varies by deployment; everything else is a constant beside its owner."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Annotated, Final, Self, cast
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from webauth.proxies import TrustedProxies

from presentator.adapters.postgres.engine import LONGEST_TIMEOUT
from presentator.adapters.speech import VoiceId, voice_speaks
from presentator.agent.settings import AgentSettings
from presentator.contracts.words import LANGUAGES, Language
from presentator.host.environment import (
    ENVIRONMENT_PREFIX,
    ConfigurationError,
    cannot_start,
    variable_of,
)
from presentator.host.worker_settings import (
    DEFAULT_BUILD_OUTPUT_MEGABYTES,
    DEFAULT_BUILD_TIMEOUT_SECONDS,
    MINIMUM_KEY_LENGTH,
)

# Every secret this instance is handed is at least this many characters, so no
# guard of it rests on a value somebody could type.
SECRET_LENGTH: Final = 32

_INVALID_PORT: Final = "has an invalid port"
_SCOPE_TOKEN: Final = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]+")
_EMPTY_CLIENT_ID: Final = "token_clients: names an empty client id"
_VOICE_SPEAKS_OTHER: Final = "names a voice that does not speak it"
_NO_VOICE: Final = "names no voice for this offered language"
_ONE_HOSTNAME: Final = "is another hostname than public_origin"
_POSTGRES_HOLDS: Final = (
    "together with build_timeout_seconds is a timeout Postgres holds"
)
_WORKER_REACHED: Final = (
    "is set together with build_worker_key_file, and both are set when builds"
    " run in the worker"
)

_NESTED: Final = "__"
_SHIPPED_VOICES: Final[Mapping[Language, VoiceId]] = {
    "de": VoiceId.THORSTEN,
    "en": VoiceId.NORMAN,
}
# Room for a dozen talks at the bound one build may leave.
DEFAULT_BUILD_CACHE_BYTES: Final = 4 * 1024**3


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


def _over_the_shipped_voices(given: object) -> object:
    """Lay the voices a setting names over the shipped ones, language by language."""
    if isinstance(given, Mapping):
        # The field's own validation judges each entry after this.
        return {**_SHIPPED_VOICES, **cast("Mapping[str, object]", given)}
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
# Shutdown waits for a chore in flight, so a statement held by a lock would
# hold it too; every statement served, and `unpin-host`'s, is cancelled past
# this bound. The migrations run without it.
type StatementTimeoutSeconds = Annotated[
    float, Field(gt=0, le=LONGEST_TIMEOUT.total_seconds(), allow_inf_nan=False)
]
_STATEMENT_TIMEOUT_SECONDS: Final = 10.0


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
    # The Postgres every store is served from. The URL names no password: the
    # file is its one owner, read once at start, so it never rides the
    # environment. A deployment fact: no default.
    database_url: PostgresUrl
    database_password_file: AbsolutePath
    database_statement_timeout_seconds: StatementTimeoutSeconds = (
        _STATEMENT_TIMEOUT_SECONDS
    )
    # The Redis every pod shares for its limits and its ask lease; it keeps
    # nothing durable. A password may ride the URL, which is why it is a secret,
    # or live in a file, read once at start, like the database's. A
    # deployment fact: no default.
    redis_url: RedisUrl = Field(repr=False)
    redis_password_file: AbsolutePath | None = None
    mirrors: Path = Path("mirrors")
    # The bucket every process keeps the built talks in; the endpoint, region
    # and key come from the environment boto3 reads. A deployment fact: no default.
    s3_bucket: str = Field(min_length=1)
    # Where built talks are built and cached on this machine, past how many
    # bytes the cache lets the least recently served go, and the Node project
    # whose Slidev builds them.
    builds: Path = Path("builds")
    build_cache_bytes: int = Field(default=DEFAULT_BUILD_CACHE_BYTES, gt=0)
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
    # The ports a socket can bind; another number would fail only at the bind.
    port: int = Field(default=8000, ge=0, le=65535)
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
    # The voice an answer is spoken in, by the offered language of the answer:
    # each a catalogue voice that speaks that language. An entry set here takes
    # the place of the shipped one; every offered language needs one.
    speech_voices: Annotated[
        Mapping[Language, VoiceId], BeforeValidator(_over_the_shipped_voices)
    ] = _SHIPPED_VOICES
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
    def postgres(self) -> PostgresDatabase:
        """The Postgres this instance serves from, its password read from its file."""
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

    @model_validator(mode="after")
    def _voices_speak_their_language(self) -> Self:
        for language in LANGUAGES:
            voice = self.speech_voices.get(language)
            if voice is None or not voice_speaks(voice, language):
                field = f"speech_voices{_NESTED}{language}"
                rule = _NO_VOICE if voice is None else _VOICE_SPEAKS_OTHER
                raise _FieldRefusedError(rule, field=field)
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
    def _statement_bound_fits_postgres(self) -> Self:
        # A wait for the refresh runs under both bounds added up.
        longest = LONGEST_TIMEOUT.total_seconds()
        if (
            self.database_statement_timeout_seconds + self.build_timeout_seconds
            > longest
        ):
            field = "database_statement_timeout_seconds"
            raise _FieldRefusedError(_POSTGRES_HOLDS, field=field)
        return self

    @model_validator(mode="after")
    def _two_hostnames(self) -> Self:
        # The cookies of the two origins stay apart only on two hostnames.
        if urlsplit(self.talk_origin).hostname == urlsplit(self.public_origin).hostname:
            raise _FieldRefusedError(_ONE_HOSTNAME, field="talk_origin")
        return self


class DatabaseSettings(BaseSettings):
    """What `presentator migrate` and `unpin-host` read, so they need no other."""

    model_config = SettingsConfigDict(
        env_prefix=ENVIRONMENT_PREFIX, hide_input_in_errors=True
    )

    # The URL names no password: the file is its one owner, and it is read once
    # at start, so it never rides the environment.
    database_url: PostgresUrl
    database_password_file: AbsolutePath
    database_statement_timeout_seconds: StatementTimeoutSeconds = (
        _STATEMENT_TIMEOUT_SECONDS
    )

    @cached_property
    def postgres(self) -> PostgresDatabase:
        """The database, with its password read once from its file."""
        return _postgres_database(self.database_url, self.database_password_file)


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

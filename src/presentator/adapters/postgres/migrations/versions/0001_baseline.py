"""Today's eight tables, in the shape a started instance holds."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None

# Paths stay text: they are the places on the machine that wrote them.
_PERSON_OR_AGENT = "kind IN ('person', 'agent')"


def upgrade() -> None:
    """Create every table; a migration only adds, so there is no downgrade."""
    _create_users()
    _create_sessions()
    _create_instance_settings()
    _create_person_preferences()
    _create_sources()
    _create_source_key_drafts()
    _create_decks()
    _create_source_runs()


def _create_users() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False, unique=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="person"),
        sa.CheckConstraint(_PERSON_OR_AGENT),
    )


def _create_sessions() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("id_token", sa.Text()),
        sa.Column("account_language", sa.Text()),
    )


def _create_instance_settings() -> None:
    op.create_table(
        "instance_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1"),
    )


def _create_person_preferences() -> None:
    op.create_table(
        "person_preferences",
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("theme", sa.Text()),
    )


def _create_sources() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("hook_secret_hash", sa.LargeBinary()),
        sa.Column("owner_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ssh_public_key", sa.Text()),
        sa.Column("folder", sa.Text(), nullable=False, server_default=""),
    )


def _create_source_key_drafts() -> None:
    op.create_table(
        "source_key_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Text(),
            sa.ForeignKey("users.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("encrypted_private_key", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )


def _create_decks() -> None:
    op.create_table(
        "decks",
        sa.Column("slug", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("changed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("owner_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("active_build", sa.Text()),
        sa.Column("pdf_export", sa.Text()),
        sa.Column("built_commit", sa.Text()),
        sa.Column("built_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("attempt_commit", sa.Text()),
        sa.Column("attempt_started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("attempt_outcome", sa.Text()),
        sa.Column("attempt_failure", sa.Text()),
        sa.Column("attempt_reason", sa.Text()),
        sa.Column("attempt_written_megabytes", sa.Integer()),
        sa.Column("attempt_allowed_megabytes", sa.Integer()),
        sa.Column("attempt_allowed_entries", sa.Integer()),
        sa.Column("built_toolchain", sa.Text()),
        sa.Column("attempt_toolchain", sa.Text()),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True)),
    )


def _create_source_runs() -> None:
    op.create_table(
        "source_runs",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text()),
        sa.Column("reason", sa.Text()),
    )

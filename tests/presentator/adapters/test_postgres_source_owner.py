"""A source created for a member through the real Postgres stores."""

from dataclasses import replace

from sqlalchemy import Engine

from presentator.application.decks import AddedSource, NewSourceDraft
from presentator.contracts.decks import ConnectionCheckResult
from presentator.contracts.models import Role, User
from tests.presentator.adapters.support.key_draft_support import a_key_drafts_store
from tests.presentator.application.support.decks_support import decks_over
from tests.support.deck_source_support import (
    OWNER as ADMIN,
)
from tests.support.deck_source_support import (
    a_source_database,
    a_source_store,
)
from tests.support.people import seed_user

_MEMBER = User(id="the-member", username="anna", role=Role.USER)
_ADDRESS = "git@example.invalid:decks.git"


def test_an_ssh_source_an_admin_creates_for_a_member_belongs_to_the_member(
    engine: Engine,
) -> None:
    database = a_source_database(engine)
    seed_user(database, _MEMBER)
    sources = a_source_store(database)
    drafts = a_key_drafts_store(database)
    decks = replace(decks_over(), sources=sources, key_drafts=drafts)
    draft = decks.source_key_draft(owner_id=ADMIN.id)
    checked = decks.check_connection(
        url=_ADDRESS, secret="", owner_id=ADMIN.id, key_draft_id=draft.id
    )
    assert isinstance(checked, ConnectionCheckResult)

    added = decks.add_source(
        NewSourceDraft(
            name="talks",
            url=_ADDRESS,
            secret="",
            fingerprint=checked.fingerprint or "",
            key_draft_id=draft.id,
        ),
        owner_id=_MEMBER.id,
        prover_id=ADMIN.id,
    )

    assert isinstance(added, AddedSource)
    assert [source.owner_id for source in sources.all()] == [_MEMBER.id]

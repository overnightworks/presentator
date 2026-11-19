"""The one place this product names webauth's session table.

The extra's `session_table` puts `sessions` on a `MetaData` that holds the
account id column it references; the accounts table is otherwise read by
`accounts.py`, so only its id is declared here. The migrations carry their own
explicit operations, and `tests` hold them equal to this table.
"""

from typing import Final

from sqlalchemy import Column, MetaData, Table, Text
from webauth.postgres import session_table

_METADATA: Final = MetaData()
_USERS: Final = Table("users", _METADATA, Column("id", Text, primary_key=True))

SESSIONS: Final = session_table(_METADATA, _USERS.c.id)

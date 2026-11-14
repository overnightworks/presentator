"""Alembic's environment: it runs on the connection `migrate` holds the lock with."""

from alembic import context

connection = context.config.attributes["connection"]
context.configure(connection=connection)
with context.begin_transaction():
    context.run_migrations()

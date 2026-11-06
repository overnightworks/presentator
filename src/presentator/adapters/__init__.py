"""Owns every implementation of a port.

Holds the webauth stores, the speech adapters, SQLite, the secrets, the
filesystem, the deck sources read through the git mirror, the audit log, the
gettext catalogs, the sentence splitter, the sealed build worker and the Slidev
build, and imports `presentator.ports` and `presentator.contracts` only -- never the application, the API, or the host.
"""

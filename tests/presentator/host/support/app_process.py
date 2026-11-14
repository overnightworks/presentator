"""One app process: the composition root served over an issuer it fakes.

Run with `python -m <module> <port> [<migrations directory>]` in the environment
a deployment gives the app. The parent signs a person in by writing one JSON
line to its standard input, naming the authorization address the app sent the
browser to and who signed in; the process answers with one line of the query
the issuer sends the browser back with. A migrations directory replaces this
release's history, as a release with a longer one would.
"""

import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from webauth.oidc import OidcRelyingParty

from presentator.adapters.postgres import migrate as migrations
from presentator.host.main import main
from tests.support.fake_issuer import FakeIssuer

ANSWER_PREFIX: Final = "issuer-answer "


def _answer_the_parent(issuer: FakeIssuer) -> None:
    for line in sys.stdin:
        sign_in = json.loads(line)
        query = issuer.authorize(
            sign_in["authorization_url"],
            subject=sign_in["subject"],
            groups=sign_in["groups"],
            username=sign_in["subject"],
        )
        print(ANSWER_PREFIX + query, flush=True)  # noqa: T201 - the parent reads this line


def run() -> None:
    """Serve on the given port until killed."""
    os.environ["PRESENTATOR_PORT"] = sys.argv[1]
    if len(sys.argv) > 2:  # noqa: PLR2004 - the optional second argument
        migrations.MIGRATIONS_DIRECTORY = Path(sys.argv[2])
    issuer = FakeIssuer(
        issuer=os.environ["PRESENTATOR_OIDC_ISSUER"],
        client_id=os.environ["PRESENTATOR_OIDC_CLIENT_ID"],
        now=datetime.now(UTC),
    )
    threading.Thread(target=_answer_the_parent, args=(issuer,), daemon=True).start()
    main(
        [],
        relying_party_for=lambda config: OidcRelyingParty(
            config,
            post_token_request=issuer.post_token_request,
            fetch_key_set=issuer.fetch_key_set,
            wall_clock=time.time,
            monotonic_clock=time.monotonic,
        ),
    )


if __name__ == "__main__":
    run()

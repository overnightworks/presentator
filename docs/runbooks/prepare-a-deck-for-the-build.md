# Runbook: prepare a talk authored for another Slidev setup

Symptom: a talk that builds elsewhere fails here, and its deck page shows the
toolchain's own words. Impact: the deck's author gets no new talk; the talk
that stood keeps standing. A deck folder carries no dependencies of its own:
the toolchain project owns them, and the deck's page names the theme set this
instance builds with.

## Check

Push the deck and open its page: the build ends ready. A failure shows the
toolchain's words there.

## Fix

1. A theme, addon or plugin builds only if `slidev/` carries it too. Widen
   that set through a change to `slidev/package.json`, not a push of the deck.
   Rollback: revert that change.
2. Strip a Vue component wired to a service the deck does not bring with it:
   the build has nowhere to call, and a deck's own Vue components and global
   layers run in the build. Rollback: revert the commit in the deck's
   repository.
3. Turn a remote asset named in the frontmatter, such as a background image
   fetched by URL, into a local file committed into the deck folder and named
   by a relative path: a container build never gets network. Rollback: revert
   the commit.
4. Set `fonts.provider: none` and `favicon: false` in the frontmatter: a talk
   loads nothing from other hosts, and the content security policy admits only
   its own origin. `examples/hello-deck/slides.md` shows both. Rollback:
   revert the commit.
5. Push the deck and re-run the Check.

## Escalate

When the build still fails after these steps, the failure text names what the
toolchain refused: the deck's author fixes the deck, and the instance's
operator decides whether `slidev/` carries a further theme or addon
([what a deck may build with](../decks.md)).

# presentator vision

## What presentator does

presentator lets people give talks from any computer with a browser and nothing
installed. A talk is a slide deck written as text and kept in a repository:
presentator picks up each push, builds the decks that changed, and offers each
as a projector view, a presenter view and a PDF. A push that fails to build
never costs the talk: the last good build stays on stage, and the PDF of the
last good build always downloads. Several people can use it, each with their
own decks. It should also be able to draft a deck from a topic, from material
or from a template.

Per talk, the presenter can switch on an AI co-presenter, backed by whatever AI
model the operator provides. It should hear the room and answer questions about
the deck in a spoken voice, and also give the talk alongside the presenter:
speak the slides, move through the deck on command or on its own, stop when
someone asks, answer, and carry on. It should keep the talk's pace: a spoken
answer begins within three seconds, and while it presents it speaks within a
second of moving on to the next slide. Each talk should choose its model and
voice from those the operator provides. Whenever the AI listens or answers, the
projector says so in the talk's language, and every answer is marked as made by
an AI. Everything works without the AI too.

## Who uses it today

The operator, for his own talks.

## Deliberate non-goals

- No sign-up and no passwords: who may enter, administer or use the AI is
  decided where the operator manages access, never inside presentator.
- No models of its own: presentator uses the AI models and voices the operator
  provides, and downloads, loads or manages none of them.
- No cloud speech: what the room says becomes text on the operator's own
  machines, and no browser or cloud service hears it.
- No shared links: a deck, its talk and its PDF open only for the person who
  owns them.

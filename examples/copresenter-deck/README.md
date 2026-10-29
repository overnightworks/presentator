Audience: a deck author who wants the co-presenter in their deck.

The overlay is the Slidev addon `slidev-addon-presentator`, which Presentator's
toolchain carries. Enable it with one line in the deck's headmatter:

```yaml
addons:
  - slidev-addon-presentator
```

Copy nothing. The deck builds where a toolchain carries the addon, which is a
Presentator instance; a local `slidev` needs the addon installed beside it.

A deck that used to copy `custom-nav-controls.vue`, `style.css`,
`components/CoPresenter.vue` or `public/pcm-capture-processor.js` removes those
copies in the same commit that adds the line. Slidev composes these files from
every root, so a kept copy mounts the overlay twice: two AI switches, two
microphones and two speakers.

The presenter toolbar uses the signed-in Presentator origin's relative
`/copresenter` routes. Each question names the deck it is asked from in its
`deck` field (the slug in the deck's address); Presentator answers only for a
built deck its asker owns. Presentator checks the existing session before
readiness, answers, or microphone capture and keeps the hearing lease open only
while local hearing is active. How to run the private speech service is
[the speech runbook](../../docs/runbooks/run-the-speech-service-on-the-host.md).

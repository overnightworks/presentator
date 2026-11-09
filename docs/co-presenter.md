# Co-presenter

Audience: whoever changes or operates the co-presenter, the AI that answers
beside a talk. This page holds the co-presenter's ruled sentences, each with the
ID its test cites. How a question is asked, bounded and answered is in the code;
the instance speaks and hears through the shared speech service over the internal
network `hal_speech_presentator`, and that hop's trust sentence is in
[Trust](ARCHITECTURE.md#5-crosscutting-concepts).

## What it knows and whom it answers

- **CP1** The co-presenter knows only the deck being presented: only that deck's
  words go to the model, and the model gets one tool, `show_slide`, and
  nothing else.
- **CP2** It answers only the person who owns the shown deck, and only about
  that deck. Any other deck gets the same 404 and spends no question.
- **CP3** Only members of the agent service's API group are answered; any other
  signed-in person gets "not enabled". presentator has no role or group gate of
  its own and builds nothing to choose or refuse the agent service's route.
- **CP4** A role change does not end an answer or a hearing: a demoted admin's
  live session is still answered.
- **CP5** The person reaches the model gateway as a fingerprint of issuer and
  account id, never as a name, an address or a token.

## What the presenter sees

- **CP6** The overlay shows "Not enabled", "Sign in again" or "No answer", only
  its own words. It navigates only by the model's `show_slide` call, inside the
  deck's slides, and shows no word for it. A refusal line clears at the next
  question.
- **CP7** The overlay's speech words are "Voice offline" (the service does not
  answer, or the configured voice cannot speak), "Local hearing not ready" (hearing
  still loads) and "No hearing" (a hearing that fails or cannot open). A new
  word needs a ruling.

## What the room sees

- **CP8** The room notice shows only while the AI listens or answers.
- **CP9** Once AI is on, the projector shows the whole notice sentence large
  until the first answer plays, and after that a small corner pill. The notice
  never shrinks or covers the slide and takes no clicks.
- **CP10** The room's words follow the deck's language, as [decks](decks.md)
  DK3 rules, and the presenter's own controls stay English.
- **CP11** The notice names no provider. In German it ends „… an einen
  KI-Dienst gesendet. Welcher, steht in der Datenschutzerklärung.“ and in
  English „… sent with the slides to an AI service. The Datenschutzerklärung
  (privacy notice) says which one.“ The whole sentences are „Dieser Vortrag
  nutzt einen KI-Co-Presenter. Solange er eingeschaltet ist, wird Gesprochenes
  lokal in Text umgewandelt und mit den Folien an einen KI-Dienst gesendet.
  Welcher, steht in der Datenschutzerklärung.“ and "This talk uses an AI
  co-presenter. While it is switched on, speech is converted to text locally and
  sent with the slides to an AI service. The Datenschutzerklärung (privacy
  notice) says which one."
- **CP12** Every answer event of text, sentence, audio and done carries
  `"ai_generated": true`; `error` and `slide`, the control events, do not; and
  nothing switches it off.

## Where it runs and when it stops

- **CP13** The microphone is allowed only on the talk host.
- **CP14** Signing out, an expired session or a lost account ends the hearing
  and a running answer, queued and playing audio included, within one second.

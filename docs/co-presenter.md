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

- **CP6** The overlay shows only its own words: "Not enabled", "Sign in again",
  "Busy, try again in N s" for a busy gateway that names its wait (N its seconds
  rounded up), "Busy, try again soon" for one that names none, "Budget used up"
  for a spent budget, and "No answer" for every other stop. It navigates only
  by the model's `show_slide` call, inside the deck's slides, and shows no word
  for it. A refusal line clears at the next question. Each word has a German
  version in the overlay's catalog.
- **CP7** The overlay's speech words are "Voice offline" (the service does not
  answer, or a configured voice cannot speak), "Local hearing not ready" (hearing
  still loads) and "No hearing" (a hearing that fails or cannot open). A hearing
  the server ends in order, as at sign-out, shows no word. Two more words
  complete the set: "Unreachable" when `/who` fails for any other reason, and
  "No speech" when an answer's sound cannot play. A fault word shows in the
  language the overlay already knows from an earlier `/who` answer, and in
  English only while no `/who` has ever answered. Each word has a German version
  in the overlay's catalog. A new word needs a ruling.
- **CP15** The presenter view carries Home at its top left as an icon button and
  the AI switch at its top right; neither sits in the bottom navigation bar, and
  nothing of the co-presenter floats over the bottom of the view. The switch is
  labelled AI, grey when off and green when on, with no word beside it; a word
  shows beside it only for a fault, in a warning colour. A screen reader hears
  "Home" for the icon button and "Presenter on" or "Presenter off" for the
  view's controls, each with a German version in the overlay's catalog.

## What the room sees

- **CP8** The room notice shows only while the AI listens or answers.
- **CP9** Once the AI listens, the projector shows the whole notice sentence as
  a band along the slide's bottom edge for ten seconds, and after that a small
  corner pill for as long as the AI listens or answers; the pill says whether
  it listens or answers. The band shows again each time the AI is switched on,
  and on any window that starts showing the AI as on, for that window's first
  ten seconds. An answer that plays during the band does not shorten it. The
  presenter's own preview shows only the pill. The notice never shrinks or
  moves the slide and takes no clicks; the band covers the slide's bottom strip
  only for its ten seconds.
- **CP10** The presenter's own controls and fault words follow the presenter's
  account language (German or English, English as fallback); the room notice and
  pills keep following the deck's language, as [decks](decks.md) DK3 rules, and
  so do the hearing and the questions. `/who` names the account language beside
  the deck's. Until it has answered the overlay shows only the switch. The
  overlay loads the words of the language the first `/who` answer names, at start
  or at switch-on if the start's `/who` failed, and keeps them; a fault word is
  English only while no `/who` has ever answered.
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
  nothing switches it off. The answer is spoken by the voice of its language.

## Where it runs and when it stops

- **CP13** The microphone is allowed only on the talk host.
- **CP14** Signing out, an expired session or a lost account ends the hearing
  and a running answer, queued and playing audio included, within one second,
  with no fault word.

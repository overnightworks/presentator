# Co-presenter

Audience: whoever changes or operates the co-presenter, the AI that answers
beside a talk. This page holds the co-presenter's ruled sentences, each with the
ID its test cites. How a question is asked, bounded and answered is in the code;
the instance speaks and hears through the shared speech service, a service inside
the cluster, and that hop's trust sentence is in
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
  account language when the lobby's catalogs offer it, else the browser's, else
  the catalogs' source language, as the lobby's [LB1](lobby.md) and
  [decks](decks.md) DK3; the room notice and pills keep following the deck's
  language, as [decks](decks.md) DK3 rules, and so does the hearing request; a
  question carries the language its utterance was heard in. `/who` names the
  language of the controls beside the deck's. Until it has answered the overlay
  shows only the switch. The overlay loads the words of the language the first
  `/who` answer names, at start or at switch-on if the start's `/who` failed, and
  keeps them; a fault word is English only while no `/who` has ever answered.
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
  nothing switches it off. The answer is written and spoken in the heard
  question's language, which the hearing reports for each finished utterance;
  a question heard in none is answered in the asking person's language of the
  controls (CP10), never the deck's.

## Where it runs and when it stops

- **CP13** The microphone is allowed only on the talk host.
- **CP14** Signing out, an expired session or a lost account ends the hearing
  and a running answer, queued and playing audio included, within one second,
  with no fault word.
- **CP16** A stopping web pod turns unready, takes no new answer or hearing,
  lets a running answer finish and closes each hearing with 1012 (service
  restart); the half-heard sentence is lost and the speaker repeats it. The
  overlay opens the hearing again at the same address, which any ready pod
  answers, with no fault word; any other close shows "No hearing". On the loop
  route a running decision ends, no new one starts, and the talk closes 1012 as
  soon as no decision runs on it, or at the drain's limit
  (`PRESENTATOR_TALK_DRAIN_SECONDS`), which cuts a decision still running.
- **CP17** A final transcript heard while an answer is spoken or streamed stops
  that answer at once, its queued and playing audio included, and is asked as
  the next question. This holds until the talk-over rule decides what an
  interjection does; from then that rule says whether the answer stops, finishes
  its sentence or goes on, and what becomes of the heard words. Until then a
  backchannel final such as "Mhm." stops the answer too.

**What the log keeps of a hearing.** Each hearing is a trace of its own and
writes one `hearing_session.ended`, with its language, how many openings and
utterances it had, how it ended (`ending`) or why it failed (`reason`, with the
service's `status`, `code` or `close` where one exists). One event is one shape:
a browser that leaves ends `outcome` ok with `ending` `browser_left`, also when
the service closed in the same moment, and a session that ends (CP14) ends ok
with `ending` `app_ended`, mid-hearing and at a reopening's token read alike. A
`cancelled` outcome is a hearing whose task was cancelled from outside, never a
browser that left; a talk the stop closes ends `cancelled` too. Each opening of
the service's socket writes one
`hearing_opening.ended` with its `attempt`; a reopening that opened names its
`cause` and how long the hearing went unheard (`unheard_ms`), one that ended
before it opened names neither, and a reopening attempt that did not open ends
as `attempt_failed`. A message from the overlay that the hearing cannot read
ends it `refused` with `reason` `unreadable_message`. The speech client reports
no opened session, so a first opening counts as open at its first event and its
duration is an upper bound. A failure nobody expects, which closes the socket
1011, writes one `hearing.failed` with its `error_type` and the `frames` of the
error and of every error it holds or came from (each one's type, then its
module, function and line, never a message or a file path), and a decision it
cuts ends `failed` with the same `error_type`. No line holds a transcript,
token, address or path.
On `/copresenter/hear`, each final transcript hands the overlay the service's
trace of its turn; the overlay asks under that trace with a span of its own and
its flags, or under a new trace with flags `00` when no valid one came (W3C
Trace Context, a higher version included), so the turn's hearing, ask and answer
lines share one `trace_id`, and the next turn never reuses a failed one's. On the
loop route `/copresenter/talk`, the hearing's lines and `echo.dropped` sit in the
talk socket's own trace, while each decision sits in the trace of the line that
woke it.

**What the log keeps of an answer.** A gateway run that streamed an event
writes one `gateway_run.ended` with a count per event type, its `duration_ms`,
its `outcome` and the run's span, and the `move_ms` of its first slide move
(null when nothing moved). The `outcome` is `ok` when the stream ended, the
stop's `refused` or `failed` with its `reason` when the gateway stopped the run
after an event, `cancelled` when the stream was closed before its end, and
`failed` with its `error_type` when another exception ended it. Every
turn that stops short of its answer writes one `gateway_turn.stopped` with the
same fields whatever its reason: `outcome` (`refused` or `failed`), `reason` (the
gateway's code, or `request_invalid` for an ask whose message never became a
request), `stop_outcome` (the wording it ends in), `detail` and `retry_after_s`
(null where none exists). The loop route writes one `answer.ended` for each
decision, in the trace of the heard line that woke it, and one `echo.dropped`
for each heard line that repeats the answer just played, without its text. A
decision the model kept silent (it answered `[silent]` and played no sentence)
ends `ok` with `reason` `silent` and no `first_output_ms`; an answer the model
gave that the pod did not speak whole (speech failed, or the browser refused a
sound) ends `failed` with `reason` `unspoken`; it carries a `first_output_ms`
when a sound was sent before the cut. No line holds the question, the answer
or a transcript.

**How soon an answer begins.** The answer start is one turn's time from the end
of speech to the first audio its answer sends, the figure behind the vision's
three seconds. A line's start is its `ts` less its `duration_ms`. The end of
speech is the start of the speech service's `turn_end_wait.ended` in the turn's
trace, the wait from the first silent audio to the turn's end; the first audio
is the start of `answer.ended` plus its `first_output_ms`, which counts from the
ask, or from the decision's start on the loop route. `uv run --locked python
scripts/answer_start.py <files>` (or the lines on standard input) reads both
services' JSON lines of a release, joins them by `trace_id` and prints `turns`,
`p50_ms` and `p95_ms` (nearest rank), then one `unjoined <trace_id> <reason>`
per trace it leaves out: `no_end_of_speech` (an ask under a fresh trace),
`no_answer` (a turn nobody asked), `silent` (a decision the model kept silent,
which is no fault), `no_first_audio` (an answer that sent no audio, such as an
unspoken one whose speech failed before any sound; one whose sound the browser
refused did send audio and joins as a figure), `repeated` or `malformed`. It
exits 1 when no turn joins. The two instants come from two processes' wall
clocks, so the figure carries their offset, and it ends where the server sends
the first audio, before the network and the browser's playback.

`scripts/answer_start_live <item number>` reads the last 24 hours of both
services' logs from the cluster, passes them to `scripts/answer_start.py` and
posts its output as a comment on the item whose number it takes.

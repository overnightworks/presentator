<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useNav } from '@slidev/client'
import { createPlayer, HopClose, startCapture } from 'overnightworks-speech-browser'
// `no-inline`: the file is under Vite's inline limit, and a `data:` URL would
// break the production CSP.
import WORKLET_URL from 'overnightworks-speech-browser/capture-worklet.js?url&no-inline'
import { controlLanguage, controlWords, currentWords, useAccountLanguage } from '../controls-words.js'
import { publish } from '../room-notice.js'

const { currentSlideNo, go, total } = useNav()

const COPRESENTER = '/copresenter'
// The host delivers this deck at `/deck/<slug>/`, so the base names its slug.
// Vite refuses a base with a raw `%`, so a folder `50%off` arrives as `50%25off`.
const DECK_SLUG = decodeURIComponent(
  import.meta.env.BASE_URL.split('/').findLast(Boolean) ?? '',
)

// The overlay shows only the catalog's words, never a message the server sent.
const NO_ANSWER = 'controls.noAnswer'
const NOT_ENABLED = 'controls.notEnabled'
const VOICE_OFFLINE = 'controls.voiceOffline'
const SIGN_IN_AGAIN = 'controls.signInAgain'
const BUSY_SOON = 'controls.busySoon'
const BUDGET_USED_UP = 'controls.budgetUsedUp'
// WebSocket close code 1012: the server is restarting and another one serves.
const SERVICE_RESTART = 1012
const HEARING_NOT_READY = 'controls.hearingNotReady'
const NO_HEARING = 'controls.noHearing'
const NO_SPEECH = 'controls.noSpeech'
const UNREACHABLE = 'controls.unreachable'
// The answer stream's error reasons, owned by AnswerStopReason on the server.
const NOT_ENABLED_REASON = 'not-enabled'
const BUSY_REASON = 'busy'
const BUDGET_SPENT_REASON = 'budget-spent'
// The server's `detail` values are compared as data, never shown.
const SIGN_IN_AGAIN_DETAILS = new Set(['Sign in again', 'Not authenticated'])

class WhoRefused extends Error {
  constructor(status) {
    super('co-presenter unavailable')
    this.status = status
  }
}

async function refusalLine(response) {
  if (response.status !== 401) return faultLine(NO_ANSWER)
  try {
    const body = await response.json()
    return faultLine(SIGN_IN_AGAIN_DETAILS.has(body?.detail) ? SIGN_IN_AGAIN : NO_ANSWER)
  } catch {
    return faultLine(NO_ANSWER)
  }
}

function busyLine(waitSeconds) {
  const named = typeof waitSeconds === 'number' && Number.isFinite(waitSeconds) && waitSeconds > 0
  return named ? currentWords()['controls.busyIn']({ seconds: String(Math.ceil(waitSeconds)) }) : faultLine(BUSY_SOON)
}

function faultLine(key) {
  return currentWords()[key]
}

const words = computed(currentWords)

function errorEventLine(data) {
  switch (data?.reason) {
    case NOT_ENABLED_REASON:
      return faultLine(NOT_ENABLED)
    case BUSY_REASON:
      return busyLine(data.retry_after_s)
    case BUDGET_SPENT_REASON:
      return faultLine(BUDGET_USED_UP)
    default:
      return faultLine(NO_ANSWER)
  }
}

const on = ref(false)
const heard = ref('')
const answer = ref('')
const error = ref('')
const speaking = ref(false)
// What the room can observe, which room-notice.js turns into band and pill:
// the capture is streaming the microphone and an answer's audio is playing.
const capturing = ref(false)
const playing = ref(false)

let mediaStream = null
let capture = null
let askAbort = null
let player = null
// The sounds handed to the player that have not ended; `speaking` is true while any is.
const handedOver = new Set()
// The sound the player is playing now; `playing` follows it, so a queued sound that ends first leaves it on.
let playingSound = null
let spokenText = ''
let unvoicedSentence = ''
// The deck's language, which the server reports before the hearing opens. It
// drives the hearing request and the room facts; a question carries the
// language of its utterance, and the controls' words the account's.
let deckLanguage = ''
let activationGeneration = 0

function publishRoomFacts() {
  publish({
    capturing: capturing.value,
    playing: playing.value,
    language: deckLanguage,
  })
}
// Synchronous, so a window that turns off has said so when its handler returns.
watch([capturing, playing], publishRoomFacts, { flush: 'sync' })

function snapshot() {
  const tracks = mediaStream ? mediaStream.getTracks() : []
  return {
    on: on.value,
    heard: heard.value,
    answer: answer.value,
    error: error.value,
    speaking: speaking.value,
    micLive: tracks.some((track) => track.readyState === 'live'),
    capturing: capturing.value,
    playing: playing.value,
  }
}

function stillActive(generation) {
  return on.value && generation === activationGeneration
}

function setOn(value) {
  if (value) void turnOn()
  else turnOff()
}

function whoUrl() {
  return `${COPRESENTER}/who?deck=${encodeURIComponent(DECK_SLUG)}`
}

// The controls' language is the account's, which only `/who` knows, so it is
// asked once at start. Turning the AI on asks again and shows why it fails.
async function learnAccountLanguage() {
  try {
    const response = await fetch(whoUrl())
    if (response.ok) await useAccountLanguage((await response.json()).interface_language)
  } catch {
    // The switch still works; turning it on asks `/who` again and shows the fault.
  }
}

async function turnOn() {
  const generation = ++activationGeneration
  on.value = true
  error.value = ''
  heard.value = ''
  answer.value = ''
  spokenText = ''
  let hearingReady
  let fault = null
  try {
    hearingReady = await readWhoAtSwitchOn()
  } catch (error_) {
    fault = error_
  }
  if (!stillActive(generation)) return
  if (fault) {
    error.value = faultLine(fault instanceof WhoRefused && fault.status === 503 ? VOICE_OFFLINE : UNREACHABLE)
    return
  }
  if (hearingReady) {
    await startLocalHear(generation)
    return
  }
  fail(faultLine(HEARING_NOT_READY))
}

// Takes the deck's and the account's language from `/who`, which a switch-off
// meanwhile does not undo, and tells whether the hearing is ready.
async function readWhoAtSwitchOn() {
  const response = await fetch(whoUrl())
  if (!response.ok) throw new WhoRefused(response.status)
  const report = await response.json()
  deckLanguage = report.language
  await useAccountLanguage(report.interface_language)
  return report.speech?.hearing?.ready
}

function turnOff() {
  stopWork()
  on.value = false
  error.value = ''
}

function fail(message) {
  stopWork()
  on.value = true
  error.value = message
}

function stopWork() {
  activationGeneration += 1
  if (askAbort) {
    askAbort.abort()
    askAbort = null
  }
  releaseCapture()
  invalidatePlayback()
}

async function startLocalHear(generation) {
  try {
    // Echo cancellation and noise suppression stay on as a second layer and are
    // not relied on: the room's speakers are not the browser's loopback. The
    // transcript filter in isEcho is the first layer.
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    })
    if (!stillActive(generation)) {
      stopStream(stream)
      return
    }
    mediaStream = stream
    await openHearing(stream, generation)
  } catch {
    endHearing(generation, null, null)
  }
}

// One hearing connection on the microphone the switch holds. The same URL
// serves a reconnect, which the platform routes to any ready pod.
async function openHearing(stream, generation) {
  const running = startCapture({
    stream,
    // `http:` becomes `ws:`, `https:` becomes `wss:`.
    hopUrl: `${window.location.origin.replace(/^http/, 'ws')}${COPRESENTER}/hear?language=${deckLanguage}`,
    workletUrl: WORKLET_URL,
    onMessage: onHeard,
  })
  capture = running
  void running.ended.then((end) => endHearing(generation, end, stream))
  if (await running.started === 'streaming' && stillActive(generation) && capture === running) {
    capturing.value = true
  }
}

// The one place that says what a lost hearing shows: nothing for the server's
// orderly close, nothing but a new connection for a pod that restarts (the
// sentence being heard is lost), "No hearing" for every other end. `end` is
// what the capture reports, or null when no capture ran to report one.
function endHearing(generation, end, stream) {
  if (!stillActive(generation)) return
  if (end?.kind === 'socket' && end.code === SERVICE_RESTART) {
    capturing.value = false
    void openHearing(stream, generation)
  } else if (end?.kind === 'hop' && end.close === HopClose.NORMAL) turnOff()
  else fail(faultLine(NO_HEARING))
}

// The capture hands over no message after it ends, and the server sends each
// transcript as a JSON text message.
function onHeard(data) {
  const payload = JSON.parse(data)
  if (!payload.text) return
  heard.value = payload.text
  if (payload.final) onFinal(payload.text, payload.language, payload.traceparent)
}

function stopStream(stream) {
  if (mediaStream === stream) mediaStream = null
  for (const track of stream.getTracks()) track.stop()
}

function releaseCapture() {
  capturing.value = false
  if (capture) {
    capture.stop()
    capture = null
  }
  if (mediaStream) stopStream(mediaStream)
}

function onFinal(text, utteranceLanguage, heardTraceparent) {
  if (!on.value || speaking.value) return
  if (isEcho(text, spokenText)) return
  void ask(text, utteranceLanguage, heardTraceparent)
}

function isEcho(text, spoken) {
  if (!spoken) return false
  const words = (value) => new Set(value.toLowerCase().split(/\s+/).filter((w) => w.length > 2))
  const a = words(text)
  const b = words(spoken)
  let overlap = 0
  for (const word of a) if (b.has(word)) overlap += 1
  // A text of short words only divides 0 by 0, and NaN is no echo.
  return overlap / a.size >= 0.5
}

const TRACEPARENT = /^00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$/
const INVALID_TRACE_ID = '0'.repeat(32)

// One W3C trace per turn, so the turn's hearing, ask and answer lines share a
// trace_id: the heard turn's trace when the hearing sent a valid one, else a new
// one. The value lives only in its ask, so a failed turn leaves none behind.
function turnTraceparent(heardTraceparent) {
  const heardTraceId = TRACEPARENT.exec(heardTraceparent ?? '')?.[1]
  const traceId =
    heardTraceId && heardTraceId !== INVALID_TRACE_ID ? heardTraceId : randomHex(16)
  return `00-${traceId}-${randomHex(8)}-01`
}

function randomHex(byteCount) {
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(byteCount))
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function ask(text, utteranceLanguage, heardTraceparent) {
  invalidatePlayback()
  if (askAbort) askAbort.abort()
  const controller = new AbortController()
  askAbort = controller
  answer.value = ''
  error.value = ''
  spokenText = ''
  unvoicedSentence = ''
  try {
    const response = await fetch(`${COPRESENTER}/ask`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        traceparent: turnTraceparent(heardTraceparent),
      },
      body: JSON.stringify({
        said: text,
        slide: currentSlideNo.value,
        language: utteranceLanguage,
        deck: DECK_SLUG,
      }),
      signal: controller.signal,
    })
    if (!response.ok) {
      error.value = await refusalLine(response)
      return
    }
    await readSse(response.body, controller.signal)
  } catch (err) {
    if (err.name === 'AbortError') return
    error.value = faultLine(NO_ANSWER)
  }
}

async function readSse(body, signal) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (!signal.aborted) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''
    for (const block of parts) applySse(block)
  }
}

// The server writes each event as one `event:` line and one `data:` line of
// JSON; a block it did not write throws, and the answer shows "No answer".
const SSE_EVENT = /^event: (\S+)\ndata: (.*)$/

function applySse(block) {
  const [, kind, dataLine] = SSE_EVENT.exec(block)
  const data = JSON.parse(dataLine)
  if (kind === 'text' && data.text) answer.value += data.text
  if (kind === 'sentence' && data.text) unvoicedSentence = data.text
  if (kind === 'audio' && data.wav_b64) {
    spokenText = `${spokenText} ${unvoicedSentence}`.trim()
    unvoicedSentence = ''
    enqueueWav(data.wav_b64)
  }
  if (kind === 'done' && data.text) answer.value = data.text
  if (kind === 'error') error.value = errorEventLine(data)
  if (kind === 'slide' && isShownSlide(data.number)) go(data.number)
}

// Slidev's `go` does not check the range, so an unknown slide stays unshown.
function isShownSlide(number) {
  return Number.isInteger(number) && number >= 1 && number <= total.value
}

function enqueueWav(b64) {
  player ??= startPlayer()
  if (!player) return
  const bytes = Uint8Array.from(atob(b64), (c) => c.codePointAt(0))
  const playback = player.play(bytes.buffer)
  handedOver.add(playback)
  speaking.value = true
  void playback.started.then((start) => {
    if (start.kind === 'playing' && handedOver.has(playback)) {
      playingSound = playback
      playing.value = true
    }
  })
  void playback.ended.then((end) => {
    if (!handedOver.delete(playback)) return
    if (playingSound === playback) {
      playingSound = null
      playing.value = false
    }
    speaking.value = handedOver.size > 0
    if (end.kind === 'failed' || end.kind === 'refused') error.value = faultLine(NO_SPEECH)
  })
}

function startPlayer() {
  try {
    return createPlayer()
  } catch {
    error.value = faultLine(NO_SPEECH)
    return null
  }
}

function invalidatePlayback() {
  handedOver.clear()
  playingSound = null
  player?.stop()
  speaking.value = false
  playing.value = false
}

onMounted(() => {
  window.addEventListener('pagehide', turnOff)
  window.__copresenter = { snapshot }
  void learnAccountLanguage()
})
onUnmounted(() => {
  window.removeEventListener('pagehide', turnOff)
  turnOff()
  player?.close()
  player = null
  // Vue stops this component's watchers before onUnmounted runs.
  publishRoomFacts()
  delete window.__copresenter
})
</script>

<template>
  <aside
    class="copresenter"
    :lang="controlLanguage"
    :data-on="on"
    :aria-label="controlWords ? (on ? words['controls.presenterOn'] : words['controls.presenterOff']) : undefined"
  >
    <span
      v-if="error"
      class="fault"
    >{{ error }}</span>
    <button
      class="switch"
      type="button"
      role="switch"
      :aria-checked="on"
      :aria-label="words['controls.switch']"
      :title="words['controls.switch']"
      @click="setOn(!on)"
    >
      <span
        class="track"
        :data-on="on"
      >
        <span class="thumb" />
      </span>
      <span class="label">{{ words['controls.switch'] }}</span>
    </button>
    <section
      v-if="on && heard"
      class="panel"
    >
      <p class="line heard">
        {{ heard }}
      </p>
      <p
        v-if="answer"
        class="line answer"
      >
        {{ answer }}
      </p>
    </section>
  </aside>
</template>

<style scoped>
.copresenter {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  column-gap: 8px;
  min-width: 0;
  color: CanvasText;
  font: 14px/1.3 system-ui, sans-serif;
}
.switch {
  grid-column: 2;
  grid-row: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 44px;
  min-height: 44px;
  padding: 0 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
}
.switch:focus-visible {
  outline: 2px solid CanvasText;
  outline-offset: 2px;
}
.panel {
  grid-column: 1 / -1;
  grid-row: 2;
  justify-self: end;
  box-sizing: border-box;
  width: min(420px, 100%);
  max-height: min(42vh, 360px);
  margin-top: 4px;
  padding: 8px 12px;
  overflow-y: auto;
  border: 1px solid color-mix(in srgb, currentcolor 20%, transparent);
  border-radius: 8px;
}
.track {
  width: 36px;
  height: 20px;
  border-radius: 999px;
  background: #6b7280;
  position: relative;
  flex-shrink: 0;
}
.track[data-on="true"] { background: #059669; }
.thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  transition: transform 120ms linear;
}
.track[data-on="true"] .thumb { transform: translateX(16px); }
.label { font-weight: 650; }
.line { margin: 6px 0 0; }
.line:first-child { margin-top: 0; }
.heard { opacity: 0.8; }
.answer { font-weight: 550; }
.fault {
  grid-column: 1;
  grid-row: 1;
  justify-self: end;
  min-width: 0;
  color: #b45309;
  font-weight: 600;
  overflow-wrap: anywhere;
}
/* Slidev marks the dark scheme with html.dark and sets no color-scheme on
   light pages, so the compiled light-dark() never reaches its light value. */
html.dark .fault { color: #fbbf24; }
@media (prefers-reduced-motion: reduce) {
  .thumb { transition: none; }
}
</style>

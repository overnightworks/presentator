<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useNav } from '@slidev/client'
import { HopClose, startCapture } from 'overnightworks-speech-browser'
// `no-inline`: the file is under Vite's inline limit, and a `data:` URL would
// break the production CSP.
import WORKLET_URL from 'overnightworks-speech-browser/capture-worklet.js?url&no-inline'
import { publish } from '../room-notice.js'

const { currentSlideNo, go, total } = useNav()

const COPRESENTER = '/copresenter'
// The host delivers this deck at `/deck/<slug>/`, so the base names its slug.
// Vite refuses a base with a raw `%`, so a folder `50%off` arrives as `50%25off`.
const DECK_SLUG = decodeURIComponent(
  import.meta.env.BASE_URL.split('/').findLast(Boolean) ?? '',
)

// The overlay shows only these lines, never a message the server sent.
const NO_ANSWER = 'No answer'
const NOT_ENABLED = 'Not enabled'
const VOICE_OFFLINE = 'Voice offline'
const SIGN_IN_AGAIN = 'Sign in again'
const BUSY_SOON = 'Busy, try again soon'
const BUDGET_USED_UP = 'Budget used up'
// The answer stream's error reasons, owned by AnswerStopReason on the server.
const NOT_ENABLED_REASON = 'not-enabled'
const BUSY_REASON = 'busy'
const BUDGET_SPENT_REASON = 'budget-spent'
const SIGN_IN_AGAIN_DETAILS = new Set(['Sign in again', 'Not authenticated'])

class WhoRefused extends Error {
  constructor(status) {
    super('co-presenter unavailable')
    this.status = status
  }
}

async function refusalLine(response) {
  if (response.status !== 401) return NO_ANSWER
  try {
    const body = await response.json()
    return SIGN_IN_AGAIN_DETAILS.has(body?.detail) ? SIGN_IN_AGAIN : NO_ANSWER
  } catch {
    return NO_ANSWER
  }
}

function busyLine(waitSeconds) {
  const named = typeof waitSeconds === 'number' && Number.isFinite(waitSeconds) && waitSeconds > 0
  return named ? `Busy, try again in ${Math.ceil(waitSeconds)} s` : BUSY_SOON
}

function errorEventLine(data) {
  switch (data?.reason) {
    case NOT_ENABLED_REASON:
      return NOT_ENABLED
    case BUSY_REASON:
      return busyLine(data.retry_after_s)
    case BUDGET_SPENT_REASON:
      return BUDGET_USED_UP
    default:
      return NO_ANSWER
  }
}

const on = ref(false)
const heard = ref('')
const answer = ref('')
const error = ref('')
const audioSeconds = ref(0)
const speaking = ref(false)
// What the room can observe, which room-notice.js turns into band and pill:
// the capture is streaming the microphone and an answer's audio is playing.
const capturing = ref(false)
const playing = ref(false)

let mediaStream = null
let capture = null
let askAbort = null
let playQueue = Promise.resolve()
let spokenText = ''
let unvoicedSentence = ''
// The deck's language, which the server reports before the hearing opens.
let language = ''
let activationGeneration = 0
let playGeneration = 0
let activeAudio = null
let finishActivePlay = null

function publishRoomFacts() {
  publish({
    capturing: capturing.value,
    playing: playing.value,
    language,
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
    audioSeconds: audioSeconds.value,
    error: error.value,
    speaking: speaking.value,
    micLive: tracks.some((track) => track.readyState === 'live'),
    capturing: capturing.value,
    playing: playing.value,
    audioPlaying: Boolean(activeAudio && !activeAudio.paused && !activeAudio.ended),
  }
}

function stillActive(generation) {
  return on.value && generation === activationGeneration
}

function setOn(value) {
  if (value) void turnOn()
  else turnOff()
}

function say(text) {
  if (!on.value) return
  heard.value = text
  void ask(text)
}

async function turnOn() {
  if (on.value) return
  const generation = ++activationGeneration
  on.value = true
  error.value = ''
  heard.value = ''
  answer.value = ''
  audioSeconds.value = 0
  spokenText = ''
  let report
  try {
    const response = await fetch(`${COPRESENTER}/who?deck=${encodeURIComponent(DECK_SLUG)}`)
    if (!stillActive(generation)) return
    if (!response.ok) throw new WhoRefused(response.status)
    report = await response.json()
    if (!stillActive(generation)) return
    language = report.language
  } catch (error_) {
    if (!stillActive(generation)) return
    error.value = error_ instanceof WhoRefused && error_.status === 503 ? VOICE_OFFLINE : 'Unreachable'
    return
  }
  if (!stillActive(generation)) return
  if (report.speech?.hearing?.ready) {
    await startLocalHear(generation)
    return
  }
  fail('Local hearing not ready')
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
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const running = startCapture({
      stream,
      hopUrl: `${scheme}//${window.location.host}${COPRESENTER}/hear?language=${language}`,
      workletUrl: WORKLET_URL,
      onMessage: (data) => onHeard(data, generation),
    })
    capture = running
    void running.ended.then((end) => endHearing(generation, end))
    if (await running.started === 'streaming' && stillActive(generation) && capture === running) {
      capturing.value = true
    }
  } catch {
    endHearing(generation, null)
  }
}

// The one place that says what a lost hearing shows: nothing for the server's
// orderly close, "No hearing" for every other end. `end` is what the capture
// reports, or null when no capture ran to report one.
function endHearing(generation, end) {
  if (!stillActive(generation)) return
  if (end?.kind === 'hop' && end.close === HopClose.NORMAL) turnOff()
  else fail('No hearing')
}

function onHeard(data, generation) {
  if (!stillActive(generation) || typeof data !== 'string') return
  let payload
  try {
    payload = JSON.parse(data)
  } catch {
    return
  }
  if (!payload.text) return
  heard.value = payload.text
  if (payload.final) onFinal(payload.text)
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

function onFinal(text) {
  if (!on.value || speaking.value) return
  if (isEcho(text, spokenText)) return
  void ask(text)
}

function isEcho(text, spoken) {
  if (!spoken) return false
  const words = (value) => new Set(value.toLowerCase().split(/\s+/).filter((w) => w.length > 2))
  const a = words(text)
  const b = words(spoken)
  if (!a.size) return false
  let overlap = 0
  for (const word of a) if (b.has(word)) overlap += 1
  return overlap / a.size >= 0.5
}

async function ask(text) {
  if (!capture) return
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
      },
      body: JSON.stringify({
        said: text,
        slide: currentSlideNo.value,
        language,
        deck: DECK_SLUG,
      }),
      signal: controller.signal,
    })
    if (!response.ok || !response.body) {
      error.value = response.ok ? NO_ANSWER : await refusalLine(response)
      return
    }
    await readSse(response.body, controller.signal)
  } catch (err) {
    if (err.name === 'AbortError') return
    error.value = NO_ANSWER
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

function applySse(block) {
  if (!on.value) return
  const kind = (block.match(/^event:\s*(\S+)/m) || [])[1]
  const dataLine = (block.match(/^data: ?(.*)$/m) || [])[1]
  if (!kind || dataLine == null) return
  let data
  try { data = JSON.parse(dataLine) } catch { return }
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
  if (!on.value) return
  const generation = playGeneration
  const bytes = Uint8Array.from(atob(b64), (c) => c.codePointAt(0))
  const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }))
  playQueue = playQueue.then(() => {
    if (generation !== playGeneration) return undefined
    return playUrl(url, generation)
  }).finally(() => URL.revokeObjectURL(url))
}

function playUrl(url, generation) {
  return new Promise((resolve) => {
    if (generation !== playGeneration) {
      resolve()
      return
    }
    const activation = activationGeneration
    const audio = new Audio(url)
    activeAudio = audio
    speaking.value = true
    let settled = false
    const settle = (failed) => {
      if (settled) return
      settled = true
      if (finishActivePlay === abortPlay) finishActivePlay = null
      if (activeAudio === audio) {
        activeAudio = null
        playing.value = false
      }
      if (generation !== playGeneration) {
        resolve()
        return
      }
      speaking.value = false
      if (failed) error.value = 'No speech'
      resolve()
    }
    const abortPlay = () => settle(false)
    finishActivePlay = abortPlay
    audio.onloadedmetadata = () => {
      if (generation !== playGeneration) return
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        audioSeconds.value += audio.duration
      }
    }
    audio.onended = () => settle(false)
    audio.onerror = () => settle(true)
    audio.play().then(() => {
      if (generation !== playGeneration || !stillActive(activation)) {
        abortPlay()
        return
      }
      playing.value = true
    }).catch(() => settle(true))
  })
}

function stopActiveAudio() {
  const audio = activeAudio
  activeAudio = null
  if (!audio) return
  audio.onended = null
  audio.onerror = null
  audio.onloadedmetadata = null
  audio.pause()
  audio.removeAttribute('src')
  audio.load()
}

function invalidatePlayback() {
  playGeneration += 1
  stopActiveAudio()
  speaking.value = false
  playing.value = false
  if (finishActivePlay) {
    const finish = finishActivePlay
    finishActivePlay = null
    finish()
  }
  playQueue = Promise.resolve()
}

onMounted(() => {
  window.addEventListener('pagehide', turnOff)
  window.__copresenter = { setOn, say, snapshot }
})
onUnmounted(() => {
  window.removeEventListener('pagehide', turnOff)
  turnOff()
  // Vue stops this component's watchers before onUnmounted runs.
  publishRoomFacts()
  delete window.__copresenter
})
</script>

<template>
  <aside
    class="copresenter"
    :data-on="on"
    :data-audio-seconds="audioSeconds"
    :aria-label="on ? 'Presenter on' : 'Presenter off'"
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
      aria-label="AI"
      title="AI"
      @click="setOn(!on)"
    >
      <span
        class="track"
        :data-on="on"
      >
        <span class="thumb" />
      </span>
      <span class="label">AI</span>
    </button>
    <section
      v-if="on && (heard || answer)"
      class="panel"
    >
      <p
        v-if="heard"
        class="line heard"
      >
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

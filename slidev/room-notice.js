import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { createSyncState, useNav } from '@slidev/client'

import { words as de } from './words/de'
import { words as en } from './words/en'

// The room notice: the presenter window decides what the AI does and writes it
// here; each window that shows the deck reads it and decides for itself how
// long it shows the whole sentence. The state travels between the windows of
// one browser and is never stored.

export const Phase = Object.freeze({
  OFF: 'off',
  LISTENING: 'listening',
  ANSWERING: 'answering',
})

// What a window can show besides a phase's pill: the whole sentence.
export const BAND = 'band'

// A window shows the whole sentence this long from when it first sees the AI live.
const BAND_MILLISECONDS = 10_000
const HEARTBEAT_MILLISECONDS = 1000
const EXPIRY_MILLISECONDS = 3000
const CLOCK_MILLISECONDS = 500

// The room's words follow the deck's language, which the speech contract
// closes to these two. They are imported statically: a projector opened late
// must show the notice at once and without the server.
const WORDS_BY_LANGUAGE = Object.freeze({ de, en })

export function derivePhase({ capturing, playing }) {
  if (!capturing) return Phase.OFF
  return playing ? Phase.ANSWERING : Phase.LISTENING
}

const sync = createSyncState(
  { phase: Phase.OFF, language: 'de', stamp: 0 },
  { phase: Phase.OFF, language: 'de', stamp: 0 },
)
// Each deck on the talk origin has its own channel.
sync.init(`presentator-room-notice:${import.meta.env.BASE_URL}`)

let publishedPhase = Phase.OFF
let derived = null
let heartbeat = null
// This window's own state is live by definition: after a stall its overdue
// timers must not make its own preview read its own stamp as stale.
const writing = ref(false)

function writeDerivedState() {
  sync.patch('phase', derived.phase)
  sync.patch('language', derived.language)
  sync.patch('stamp', Date.now())
}

// Only a window that was itself not off says it is off: every presenter window
// mounts the overlay, and a window that never listened must not clear another.
export function publish(facts) {
  const phase = derivePhase(facts)
  if (phase === Phase.OFF && publishedPhase === Phase.OFF) return
  publishedPhase = phase
  derived = { phase, language: facts.language }
  writing.value = phase !== Phase.OFF
  writeDerivedState()
  if (phase === Phase.OFF) {
    clearInterval(heartbeat)
    heartbeat = null
  } else if (heartbeat === null) {
    heartbeat = setInterval(writeDerivedState, HEARTBEAT_MILLISECONDS)
  }
}

// What this window should show now, or null: the whole sentence for the first
// BAND_MILLISECONDS after it sees the AI live (the presenter's own preview
// never), then the pill of the current phase. Expiry is computed and never
// written back, so a reader that outlives its writer cannot clear a live one.
export function useRoomNotice() {
  const { isPlaying, isPresenter, isPrintMode } = useNav()
  const now = ref(Date.now())
  const liveSince = ref(null)
  let clock = null
  onMounted(() => {
    clock = setInterval(() => { now.value = Date.now() }, CLOCK_MILLISECONDS)
  })
  onUnmounted(() => clearInterval(clock))
  const live = computed(() => {
    if (!(isPlaying.value || isPresenter.value) || isPrintMode.value) return false
    const { phase, stamp } = sync.state
    return phase !== Phase.OFF && (writing.value || now.value - stamp <= EXPIRY_MILLISECONDS)
  })
  // A window that sees the AI not live forgets the moment, so the next live
  // state starts a new band.
  watch(live, (isLive) => { liveSince.value = isLive ? Date.now() : null }, { immediate: true, flush: 'sync' })
  return computed(() => {
    if (!live.value) return null
    const { phase, language } = sync.state
    const inBand = !isPresenter.value && now.value - liveSince.value < BAND_MILLISECONDS
    const shown = inBand ? BAND : phase
    return { shown, language, text: WORDS_BY_LANGUAGE[language][`roomNotice.${shown}`] }
  })
}

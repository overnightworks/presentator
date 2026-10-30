import { computed, onMounted, onUnmounted, ref } from 'vue'
import { createSyncState, useNav } from '@slidev/client'

// The room notice (#293 E8): the presenter window decides what the room hears
// and writes it here; a projector, or the presenter's own preview, reads it.
// The state travels between the windows of one browser and is never stored.

export const Phase = Object.freeze({
  OFF: 'off',
  BANNER: 'banner',
  LISTENING: 'listening',
  ANSWERING: 'answering',
})

const HEARTBEAT_MILLISECONDS = 1000
const EXPIRY_MILLISECONDS = 3000
const CLOCK_MILLISECONDS = 500

// The room's words follow the deck's language, which the speech contract
// closes to these two.
const WORDS = Object.freeze({
  de: {
    banner: 'Dieser Vortrag nutzt einen KI-Co-Presenter. Solange er eingeschaltet ist, wird Gesprochenes lokal in Text umgewandelt und mit den Folien an einen KI-Dienst gesendet. Welcher, steht in der Datenschutzerklärung.',
    listening: 'KI hört zu',
    answering: 'KI antwortet',
  },
  en: {
    banner: 'This talk uses an AI co-presenter. While it is switched on, speech is converted to text locally and sent with the slides to an AI service. The Datenschutzerklärung (privacy notice) says which one.',
    listening: 'AI is listening',
    answering: 'AI is answering',
  },
})

export function derivePhase({ capturing, playing, answeredSinceOn }) {
  if (!capturing) return Phase.OFF
  if (playing) return Phase.ANSWERING
  if (!answeredSinceOn) return Phase.BANNER
  return Phase.LISTENING
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

// What the room should see now, or null. Expiry is computed and never written
// back, so a reader that outlives its writer cannot clear a live one.
export function useRoomNotice() {
  const { isPlaying, isPresenter, isPrintMode } = useNav()
  const now = ref(Date.now())
  let clock = null
  onMounted(() => {
    clock = setInterval(() => { now.value = Date.now() }, CLOCK_MILLISECONDS)
  })
  onUnmounted(() => clearInterval(clock))
  return computed(() => {
    if (!(isPlaying.value || isPresenter.value) || isPrintMode.value) return null
    const { phase, language, stamp } = sync.state
    if (phase === Phase.OFF || (!writing.value && now.value - stamp > EXPIRY_MILLISECONDS)) return null
    return { phase, text: WORDS[language][phase] }
  })
}

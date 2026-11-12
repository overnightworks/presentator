// Generated from the en catalog by design-catalogs. Do not edit.

import type { Words } from "./de";
// prettier-ignore
const translated = {
    "roomNotice.band": "This talk uses an AI co-presenter. While it is switched on, speech is converted to text locally and sent with the slides to an AI service. The Datenschutzerklärung (privacy notice) says which one.",
    "roomNotice.listening": "AI is listening",
    "roomNotice.answering": "AI is answering",
    "controls.switch": "AI",
    "controls.presenterOn": "Presenter on",
    "controls.presenterOff": "Presenter off",
    "controls.home": "Home",
    "controls.notEnabled": "Not enabled",
    "controls.signInAgain": "Sign in again",
    "controls.busyIn": (gaps: { readonly seconds: string }) => "Busy, try again in " + gaps.seconds + " s",
    "controls.busySoon": "Busy, try again soon",
    "controls.budgetUsedUp": "Budget used up",
    "controls.noAnswer": "No answer",
    "controls.voiceOffline": "Voice offline",
    "controls.hearingNotReady": "Local hearing not ready",
    "controls.noHearing": "No hearing",
    "controls.unreachable": "Unreachable",
    "controls.noSpeech": "No speech",
} satisfies Words;
// A translation that leaves out a gap of its source fails here.
({} as Words) satisfies typeof translated;
export const words: Words = translated;

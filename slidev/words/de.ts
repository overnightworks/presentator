// Generated from the de catalog by design-catalogs. Do not edit.

export const language = "de";
// prettier-ignore
export type Words = {
    readonly "roomNotice.band": string;
    readonly "roomNotice.listening": string;
    readonly "roomNotice.answering": string;
    readonly "controls.switch": string;
    readonly "controls.presenterOn": string;
    readonly "controls.presenterOff": string;
    readonly "controls.home": string;
    readonly "controls.notEnabled": string;
    readonly "controls.signInAgain": string;
    readonly "controls.busyIn": (gaps: { readonly seconds: string }) => string;
    readonly "controls.busySoon": string;
    readonly "controls.budgetUsedUp": string;
    readonly "controls.noAnswer": string;
    readonly "controls.voiceOffline": string;
    readonly "controls.hearingNotReady": string;
    readonly "controls.noHearing": string;
    readonly "controls.unreachable": string;
    readonly "controls.noSpeech": string;
};
// prettier-ignore
export const words: Words = {
    "roomNotice.band": "Dieser Vortrag nutzt einen KI-Co-Presenter. Solange er eingeschaltet ist, wird Gesprochenes lokal in Text umgewandelt und mit den Folien an einen KI-Dienst gesendet. Welcher, steht in der Datenschutzerklärung.",
    "roomNotice.listening": "KI hört zu",
    "roomNotice.answering": "KI antwortet",
    "controls.switch": "AI",
    "controls.presenterOn": "KI-Co-Presenter an",
    "controls.presenterOff": "KI-Co-Presenter aus",
    "controls.home": "Startseite",
    "controls.notEnabled": "Nicht aktiviert",
    "controls.signInAgain": "Melde dich erneut an",
    "controls.busyIn": (gaps: { readonly seconds: string }) => "Gerade ausgelastet, versuch es in " + gaps.seconds + " s noch einmal",
    "controls.busySoon": "Gerade ausgelastet, versuch es gleich noch einmal",
    "controls.budgetUsedUp": "Budget aufgebraucht",
    "controls.noAnswer": "Keine Antwort",
    "controls.voiceOffline": "Stimme offline",
    "controls.hearingNotReady": "Lokale Spracherkennung nicht bereit",
    "controls.noHearing": "KI hört nicht zu",
    "controls.unreachable": "Nicht erreichbar",
    "controls.noSpeech": "Keine Sprachausgabe",
};
export type Key = keyof Words;

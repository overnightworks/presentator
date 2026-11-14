import { shallowRef } from 'vue'

import { loadWords } from './words'
import { words as english } from './words/en'

// The words of the presenter's controls, in the presenter's account language,
// which only the server's `/who` answer knows. Null until a `/who` has
// answered; the controls other than the switch wait for it so that no word
// shows in the wrong language.
export const controlWords = shallowRef(null)

// The language of `currentWords()`, for the `lang` attribute of what shows them.
export const controlLanguage = shallowRef('en')

let languageKnown = false

// The first language the server names is loaded and kept; a later answer does
// not load another. A language that cannot be loaded counts as no language
// known, so a later answer may still supply one.
export async function useAccountLanguage(language) {
  if (languageKnown) return
  try {
    const words = await loadWords(language)
    if (languageKnown) return
    controlWords.value = words
    controlLanguage.value = language
    languageKnown = true
  } catch {
    // English is the one module bundled with the overlay, so the controls are
    // never missing because the named language could not be loaded.
    controlWords.value ??= english
  }
}

// The words to show now: the account's language once known, else English,
// which a fault word needs while no `/who` has answered.
export function currentWords() {
  return controlWords.value ?? english
}

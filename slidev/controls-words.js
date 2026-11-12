import { shallowRef } from 'vue'

import { loadWords } from './words'
import { words as english } from './words/en'

// The words of the presenter's controls, in the presenter's account language,
// which only the server's `/who` answer knows. Null until it has answered or
// failed; the controls other than the switch wait for it so that no word shows
// in the wrong language.
export const controlWords = shallowRef(null)

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
    languageKnown = true
  } catch {
    useEnglishUnlessKnown()
  }
}

// English is the one module bundled with the overlay, so a fault word is
// never missing because the language could not be loaded.
export function useEnglishUnlessKnown() {
  controlWords.value ??= english
}

// The words to show now: the account's language once known, else English.
export function currentWords() {
  return controlWords.value ?? english
}

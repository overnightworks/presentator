import { beforeEach, describe, expect, it, vi } from 'vitest'

import { words as german } from '../words/de'
import { words as english } from '../words/en'

type Words = typeof english | typeof german

interface ControlsWords {
  controlWords: { value: Words | null }
  currentWords: () => Words
  useAccountLanguage: (language: string) => Promise<void>
  useEnglishUnlessKnown: () => void
}

// The module keeps the account language in module state, so each case loads a
// fresh copy of it.
async function freshControlsWords(): Promise<ControlsWords> {
  vi.resetModules()
  // @ts-expect-error -- the addon ships this plain JavaScript module without declarations
  return import('../controls-words.js')
}

beforeEach(() => {
  vi.doUnmock('../words')
})

describe('the presenter controls words', () => {
  it('shows English until the account language is known', async () => {
    const { controlWords, currentWords } = await freshControlsWords()

    expect(controlWords.value).toBeNull()
    expect(currentWords()['controls.home']).toBe(english['controls.home'])
  })

  it('shows the words of the first language the server names', async () => {
    const { controlWords, currentWords, useAccountLanguage } = await freshControlsWords()

    await useAccountLanguage('de')

    expect(controlWords.value?.['controls.home']).toBe(german['controls.home'])
    expect(currentWords()['controls.home']).toBe(german['controls.home'])
  })

  it('shows the English words when the server names English', async () => {
    const { controlWords, useAccountLanguage } = await freshControlsWords()

    await useAccountLanguage('en')

    expect(controlWords.value?.['controls.home']).toBe(english['controls.home'])
  })

  it('keeps the first language when a later answer names another', async () => {
    const { currentWords, useAccountLanguage } = await freshControlsWords()

    await useAccountLanguage('de')
    await useAccountLanguage('en')

    expect(currentWords()['controls.home']).toBe(german['controls.home'])
  })

  it('keeps the language whose answer came first when two answers overlap', async () => {
    const answers = new Map<string, (words: Words) => void>()
    vi.doMock('../words', () => ({
      loadWords: (language: string) =>
        new Promise((resolve) => {
          answers.set(language, resolve)
        }),
    }))
    const { currentWords, useAccountLanguage } = await freshControlsWords()

    const germanAnswer = useAccountLanguage('de')
    const englishAnswer = useAccountLanguage('en')
    answers.get('en')!(english)
    await englishAnswer
    answers.get('de')!(german)
    await germanAnswer

    expect(currentWords()['controls.home']).toBe(english['controls.home'])
  })

  it('shows English and stays open for a later language when the named one cannot be loaded', async () => {
    const { controlWords, currentWords, useAccountLanguage } = await freshControlsWords()

    await useAccountLanguage('xx')

    expect(controlWords.value?.['controls.home']).toBe(english['controls.home'])

    await useAccountLanguage('de')

    expect(currentWords()['controls.home']).toBe(german['controls.home'])
  })

  it('does not replace a known language with English', async () => {
    const { currentWords, useAccountLanguage, useEnglishUnlessKnown } = await freshControlsWords()

    await useAccountLanguage('de')
    useEnglishUnlessKnown()

    expect(currentWords()['controls.home']).toBe(german['controls.home'])
  })

  it('sets English when no language is known', async () => {
    const { controlWords, useEnglishUnlessKnown } = await freshControlsWords()

    useEnglishUnlessKnown()

    expect(controlWords.value?.['controls.home']).toBe(english['controls.home'])
  })
})

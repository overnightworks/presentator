import { describe, expect, it } from 'vitest'

import {
  type AnswerMode,
  boxesIntersect,
  contrastRatio,
  expectFaultWordClearOfControls,
  expectPanelCoversNoSlidevControl,
  openPresenter,
  readBarBackground,
  readPaintedColours,
  registerOverlayHarness,
  saveEvidence,
  state,
  wordsOf,
} from './presenter-overlay-harness'

registerOverlayHarness()

const en = wordsOf.en
type Language = keyof typeof wordsOf

describe('co-presenter refusals', () => {
  const busy = (retry_after_s?: unknown) => ({ reason: 'busy', retry_after_s })
  const plainFaults = ['notEnabled', 'signInAgain', 'noAnswer', 'budgetUsedUp', 'busySoon'] as const
  type Fault = (typeof plainFaults)[number] | { busyIn: string }
  const wordOf = (language: Language, fault: Fault) =>
    typeof fault === 'string'
      ? wordsOf[language][`controls.${fault}`]
      : wordsOf[language]['controls.busyIn']({ seconds: fault.busyIn })
  const refusals: [string, AnswerMode, Record<string, unknown>, Fault, number[]][] = [
    ['an error event with the not-enabled reason', 'not-enabled', {}, 'notEnabled', [1024, 390]],
    ['a refused refresh (401 Sign in again)', 'signed-out-refresh', {}, 'signInAgain', [1024, 390]],
    ['an ended session (401 Not authenticated)', 'no-session', {}, 'signInAgain', [1024]],
    ['a request the origin check refuses (403)', 'forbidden', {}, 'noAnswer', [1024]],
    ['an issuer that is down (503)', 'unavailable', {}, 'noAnswer', [1024]],
    ['an error event with an unknown reason', 'error-event', { reason: 'unknown-reason' }, 'noAnswer', [1024]],
    ['a 401 whose body is not JSON', 'unreadable-401', {}, 'noAnswer', [1024]],
    ['a busy gateway naming 7 s', 'error-event', busy(7), { busyIn: '7' }, [1024, 390]],
    ['a busy gateway naming 2.2 s', 'error-event', busy(2.2), { busyIn: '3' }, [1024]],
    ['a busy gateway naming no wait', 'error-event', busy(), 'busySoon', [1024]],
    ['a busy gateway naming null', 'error-event', busy(null), 'busySoon', [1024]],
    ['a busy gateway naming zero', 'error-event', busy(0), 'busySoon', [1024]],
    ['a busy gateway naming a negative wait', 'error-event', busy(-3), 'busySoon', [1024]],
    ['a busy gateway naming a non-number', 'error-event', busy('soon'), 'busySoon', [1024]],
    ['a spent budget', 'error-event', { reason: 'budget-spent' }, 'budgetUsedUp', [1024, 390]],
  ]
  // A German account is driven at 390 px only, where the longer German words must stand whole.
  const accounts: [string, Language, number[] | undefined][] = [
    ['English account', 'en', undefined],
    ['German account', 'de', [390]],
  ]
  it.each(accounts.flatMap(([account, language, onlyWidths]) =>
    refusals.map(([situation, mode, errorEvent, fault, widths]) =>
      [`${account}, ${situation}`, language, mode, errorEvent, fault, onlyWidths ?? widths] as const)),
  )('CP6: shows only its own words for the %s', async (_situation, language, mode, errorEvent, fault, widths) => {
    const line = wordOf(language, fault)
    for (const width of widths) {
      state.answerMode = mode
      state.errorEvent = errorEvent
      state.interfaceLanguage = language
      const { browser, page } = await openPresenter(width)
      try {
        const ai = page.getByRole('switch', { name: wordsOf[language]['controls.switch'] })
        await ai.waitFor({ state: 'visible', timeout: 2_000 })
        await ai.click({ timeout: 2_000 })
        await page.getByText(line, { exact: true }).waitFor({ state: 'visible', timeout: 5_000 })
        const otherLines = plainFaults.map((key) => wordsOf[language][`controls.${key}`]).filter((other) => other !== line)
        for (const other of otherLines) expect(await page.getByText(other, { exact: true }).count()).toBe(0)
        // The busy sentence, whatever number it names, shows exactly when this case raises it.
        const anyBusyIn = new RegExp(`^${wordOf(language, { busyIn: '0' }).replace('0', String.raw`\d+`)}$`)
        expect(await page.getByText(anyBusyIn).count()).toBe(typeof fault === 'string' ? 0 : 1)
        expect(await page.getByText('the answer could not be spoken').count()).toBe(0)
        expect(page.url()).toBe(`${state.origin}/presenter/1`)
        expect(await ai.getAttribute('aria-checked')).toBe('true')
        const { homeBox, aiBox, faultBox } = await expectFaultWordClearOfControls(
          page,
          width,
          ai,
          page.getByText(line, { exact: true }),
        )
        const panelBox = await page.locator('.copresenter .panel').boundingBox()
        expect(Math.abs((faultBox.y + faultBox.height / 2) - (aiBox.y + aiBox.height / 2))).toBeLessThan(aiBox.height)
        const painted = await readPaintedColours(page)
        expect(painted.track[1], 'the track stays green').toBeGreaterThan(Math.max(painted.track[0], painted.track[2]) + 20)
        expect(contrastRatio(painted.fault!, await readBarBackground(page)), 'the word reads on the bar').toBeGreaterThanOrEqual(4.5)
        expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(width)
        expect(boxesIntersect(homeBox!, panelBox!)).toBe(false)
        expect(boxesIntersect(aiBox!, panelBox!)).toBe(false)
        await expectPanelCoversNoSlidevControl(page)
        await saveEvidence(page, `presenter-refusal-${language}-${line.replace(/\W+/g, '-')}-${width}.png`)
      } finally {
        state.answerMode = 'audio'
        state.errorEvent = {}
        await browser.close()
      }
    }
  })

  it('CP6: clears the refusal line when the next ask is answered', async () => {
    state.answerMode = 'refused-then-answered'
    const { browser, page } = await openPresenter(1024)
    try {
      const ai = page.getByRole('switch', { name: en['controls.switch'] })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await page.getByText(en['controls.noAnswer'], { exact: true }).waitFor({ state: 'visible', timeout: 5_000 })
      await page.evaluate(() => (window as unknown as {
        __copresenter: { say: (text: string) => void }
      }).__copresenter.say('And again?'))
      await page.getByText('The second ask is answered.').waitFor({ state: 'visible', timeout: 5_000 })
      expect(await page.getByText(en['controls.noAnswer'], { exact: true }).count()).toBe(0)
    } finally {
      state.answerMode = 'audio'
      await browser.close()
    }
  })
})

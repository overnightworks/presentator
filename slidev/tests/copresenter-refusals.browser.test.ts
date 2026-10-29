import { describe, expect, it } from 'vitest'

import {
  boxesIntersect,
  openPresenter,
  registerOverlayHarness,
  saveEvidence,
  state,
} from './presenter-overlay-harness'

registerOverlayHarness()

describe('co-presenter refusals', () => {
  it.each([
    ['an error event with the not-enabled reason', 'not-enabled', 'Not enabled', [1024, 390]],
    ['a refused refresh (401 Sign in again)', 'signed-out-refresh', 'Sign in again', [1024, 390]],
    ['an ended session (401 Not authenticated)', 'no-session', 'Sign in again', [1024]],
    ['a request the origin check refuses (403)', 'forbidden', 'No answer', [1024]],
    ['an issuer that is down (503)', 'unavailable', 'No answer', [1024]],
    ['an error event with an unknown reason', 'unknown-reason', 'No answer', [1024]],
    ['a 401 whose body is not JSON', 'unreadable-401', 'No answer', [1024]],
  ] as const)('Cells 1-8, 13: shows only its own words for %s', async (_situation, mode, line, widths) => {
    for (const width of widths) {
      state.answerMode = mode
      const { browser, page } = await openPresenter(width)
      try {
        const ai = page.getByRole('switch', { name: 'AI' })
        await ai.waitFor({ state: 'visible', timeout: 2_000 })
        await ai.click({ timeout: 2_000 })
        await page.getByText(line, { exact: true }).waitFor({ state: 'visible', timeout: 5_000 })
        const otherLines = ['Not enabled', 'Sign in again', 'No answer'].filter((other) => other !== line)
        for (const other of otherLines) expect(await page.getByText(other, { exact: true }).count()).toBe(0)
        expect(await page.getByText('the answer could not be spoken').count()).toBe(0)
        expect(page.url()).toBe(`${state.origin}/presenter/1`)
        expect(await ai.getAttribute('aria-checked')).toBe('true')
        const [homeBox, aiBox, panelBox] = await Promise.all([
          page.getByRole('link', { name: 'Home' }).boundingBox(),
          ai.boundingBox(),
          page.locator('.copresenter .panel').boundingBox(),
        ])
        expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(width)
        expect(boxesIntersect(homeBox!, panelBox!)).toBe(false)
        expect(boxesIntersect(aiBox!, panelBox!)).toBe(false)
        await saveEvidence(page, `presenter-refusal-${mode}-${width}.png`)
      } finally {
        state.answerMode = 'audio'
        await browser.close()
      }
    }
  })

  it('Cell 12: clears the refusal line when the next ask is answered', async () => {
    state.answerMode = 'refused-then-answered'
    const { browser, page } = await openPresenter(1024)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await page.getByText('No answer', { exact: true }).waitFor({ state: 'visible', timeout: 5_000 })
      await page.evaluate(() => (window as unknown as {
        __copresenter: { say: (text: string) => void }
      }).__copresenter.say('And again?'))
      await page.getByText('The second ask is answered.').waitFor({ state: 'visible', timeout: 5_000 })
      expect(await page.getByText('No answer', { exact: true }).count()).toBe(0)
    } finally {
      state.answerMode = 'audio'
      await browser.close()
    }
  })
})

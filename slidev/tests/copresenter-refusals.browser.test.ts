import { describe, expect, it } from 'vitest'

import {
  boxesIntersect,
  openPresenter,
  readPaintedColours,
  registerOverlayHarness,
  saveEvidence,
  state,
} from './presenter-overlay-harness'

registerOverlayHarness()

describe('co-presenter refusals', () => {
  const busy = (retry_after_s?: unknown) => ({ reason: 'busy', retry_after_s })
  it.each([
    ['an error event with the not-enabled reason', 'not-enabled', {}, 'Not enabled', [1024, 390]],
    ['a refused refresh (401 Sign in again)', 'signed-out-refresh', {}, 'Sign in again', [1024, 390]],
    ['an ended session (401 Not authenticated)', 'no-session', {}, 'Sign in again', [1024]],
    ['a request the origin check refuses (403)', 'forbidden', {}, 'No answer', [1024]],
    ['an issuer that is down (503)', 'unavailable', {}, 'No answer', [1024]],
    ['an error event with an unknown reason', 'error-event', { reason: 'unknown-reason' }, 'No answer', [1024]],
    ['a 401 whose body is not JSON', 'unreadable-401', {}, 'No answer', [1024]],
    ['a busy gateway naming 7 s', 'error-event', busy(7), 'Busy, try again in 7 s', [1024, 390]],
    ['a busy gateway naming 2.2 s', 'error-event', busy(2.2), 'Busy, try again in 3 s', [1024]],
    ['a busy gateway naming no wait', 'error-event', busy(), 'Busy, try again soon', [1024]],
    ['a busy gateway naming null', 'error-event', busy(null), 'Busy, try again soon', [1024]],
    ['a busy gateway naming zero', 'error-event', busy(0), 'Busy, try again soon', [1024]],
    ['a busy gateway naming a negative wait', 'error-event', busy(-3), 'Busy, try again soon', [1024]],
    ['a busy gateway naming a non-number', 'error-event', busy('soon'), 'Busy, try again soon', [1024]],
    ['a spent budget', 'error-event', { reason: 'budget-spent' }, 'Budget used up', [1024, 390]],
  ] as const)('CP6: shows only its own words for %s', async (_situation, mode, errorEvent, line, widths) => {
    for (const width of widths) {
      state.answerMode = mode
      state.errorEvent = errorEvent
      const { browser, page } = await openPresenter(width)
      try {
        const ai = page.getByRole('switch', { name: 'AI' })
        await ai.waitFor({ state: 'visible', timeout: 2_000 })
        await ai.click({ timeout: 2_000 })
        await page.getByText(line, { exact: true }).waitFor({ state: 'visible', timeout: 5_000 })
        const otherLines = ['Not enabled', 'Sign in again', 'No answer', 'Budget used up', 'Busy, try again soon']
          .filter((other) => other !== line)
        for (const other of otherLines) expect(await page.getByText(other, { exact: true }).count()).toBe(0)
        expect(await page.getByText(/^Busy, try again in/).count()).toBe(line.startsWith('Busy, try again in') ? 1 : 0)
        expect(await page.getByText('the answer could not be spoken').count()).toBe(0)
        expect(page.url()).toBe(`${state.origin}/presenter/1`)
        expect(await ai.getAttribute('aria-checked')).toBe('true')
        const [homeBox, aiBox, faultBox, panelBox] = await Promise.all([
          page.getByRole('link', { name: 'Home' }).boundingBox(),
          ai.boundingBox(),
          page.getByText(line, { exact: true }).boundingBox(),
          page.locator('.copresenter .panel').boundingBox(),
        ])
        // the word stands beside the switch, whole in the window and clear of Home.
        expect(faultBox!.x).toBeGreaterThanOrEqual(0)
        expect(faultBox!.x + faultBox!.width).toBeLessThanOrEqual(width)
        expect(boxesIntersect(homeBox!, faultBox!)).toBe(false)
        expect(boxesIntersect(aiBox!, faultBox!)).toBe(false)
        expect(Math.abs((faultBox!.y + faultBox!.height / 2) - (aiBox!.y + aiBox!.height / 2))).toBeLessThan(aiBox!.height)
        const painted = await readPaintedColours(page)
        expect(painted.track[1], 'the track stays green').toBeGreaterThan(Math.max(painted.track[0], painted.track[2]) + 20)
        expect(painted.fault![0], 'the word is warm').toBeGreaterThan(painted.fault![2] + 80)
        expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(width)
        expect(boxesIntersect(homeBox!, panelBox!)).toBe(false)
        expect(boxesIntersect(aiBox!, panelBox!)).toBe(false)
        await saveEvidence(page, `presenter-refusal-${line.replace(/\W+/g, '-')}-${width}.png`)
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

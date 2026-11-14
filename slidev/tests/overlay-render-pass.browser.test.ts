import type { Page } from 'playwright-chromium'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  openPageIn,
  openPresenter,
  openRoomBrowser,
  registerOverlayHarness,
  state,
  wordsOf,
} from './presenter-overlay-harness'

registerOverlayHarness()
vi.setConfig({ testTimeout: 90_000 })

// The render pass: every overlay surface that shows words, in each catalog
// language, at a phone and a desktop width.
const LANGUAGES = ['de', 'en'] as const
const WIDTHS = [390, 1440]
const CASES = LANGUAGES.flatMap((language) => WIDTHS.map((width) => ({ language, width })))
type Language = (typeof LANGUAGES)[number]

const otherLanguage = (language: Language): Language => (language === 'de' ? 'en' : 'de')

const scenarioDefaults = {
  answerMode: state.answerMode,
  errorEvent: state.errorEvent,
  whoLanguage: state.whoLanguage,
  heardOnConnect: state.heardOnConnect,
}
afterEach(() => { Object.assign(state, scenarioDefaults) })

const CONTROLS_ROOT = '.copresenter'
const NOTICE_ROOT = '.room-notice'

type Inspection = { language: string | null, text: string, overflowing: string[] }

// What one overlay root shows: its own language attribute, every word it
// shows as text or as an attribute, and the text boxes cut off or spilling
// out unless they scroll by design, plus the page itself when it scrolls
// sideways. An inline element has no box of its own, so its nearest block
// ancestor within the root is measured. A text that leaves the root or the
// window sideways overflows even where its own box grew with it.
function inspectRoot(selector: string): Inspection {
  const root = document.querySelector(selector)
  if (!root) throw new Error(`no overlay root ${selector}`)
  const scrollsByDesign = new Set(['auto', 'scroll'])
  const overflowing = new Set<string>()
  const page = document.documentElement
  if (page.scrollWidth > page.clientWidth) overflowing.add(`page: ${page.scrollWidth} > ${page.clientWidth}`)
  const rootBox = root.getBoundingClientRect()
  const shown = [root, ...root.querySelectorAll('*')]
  const words: string[] = []
  for (const element of shown) {
    const hasText = [...element.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim() !== '')
    const visible = element.getClientRects().length > 0 && getComputedStyle(element).visibility === 'visible'
    if (!visible) continue
    words.push(element.getAttribute('aria-label') ?? '', element.getAttribute('title') ?? '')
    if (!hasText) continue
    words.push(element.textContent ?? '')
    const box = element.getBoundingClientRect()
    const outside = box.left < Math.max(rootBox.left, 0) - 0.5 || box.right > Math.min(rootBox.right, window.innerWidth) + 0.5
    if (outside) overflowing.add(`${element.tagName.toLowerCase()}: ${box.left}..${box.right} leaves ${rootBox.left}..${rootBox.right}`)
    let block: Element = element
    while (block !== root && block.clientWidth === 0 && block.parentElement) block = block.parentElement
    if (block.scrollWidth > block.clientWidth && !scrollsByDesign.has(getComputedStyle(block).overflowX)) {
      const classes = [...block.classList].map((name) => `.${name}`).join('')
      overflowing.add(`${block.tagName.toLowerCase()}${classes}: ${block.scrollWidth} > ${block.clientWidth}`)
    }
  }
  return { language: root.getAttribute('lang'), text: words.join('\n'), overflowing: [...overflowing] }
}

async function expectRendersWell(page: Page, selector: string, language: Language, expectedWord: string) {
  const { language: rootLanguage, text, overflowing } = await page.evaluate(inspectRoot, selector)
  expect(rootLanguage, 'the root names the language of its words').toBe(language)
  expect(text, `the root shows its own language's word: ${expectedWord}`).toContain(expectedWord)
  for (const key of Object.keys(wordsOf[language])) {
    expect(text, `a catalog key shows: ${key}`).not.toContain(key)
  }
  expect(text).not.toMatch(/undefined|\[object Object\]|=>|\bfunction\s*\(/)
  expect(overflowing, 'text wider than its box').toEqual([])
}

// The controls follow the account's language; the deck's is the other one,
// so that a room notice or a control in the wrong language shows.
async function inControls(language: Language, width: number, expectedWord: string, show: (page: Page) => Promise<void>) {
  state.interfaceLanguage = language
  state.whoLanguage = otherLanguage(language)
  const { browser, page } = await openPresenter(width)
  try {
    await page.locator(`${CONTROLS_ROOT}[aria-label]`).waitFor({ state: 'visible' })
    await show(page)
    await expectRendersWell(page, CONTROLS_ROOT, language, expectedWord)
  } finally {
    await browser.close()
  }
}

const switchOn = (page: Page, language: Language) =>
  page.getByRole('switch', { name: wordsOf[language]['controls.switch'] }).click()

describe('the overlay render pass', () => {
  it.each(CASES)('shows the idle controls in $language at $width px without cut-off or unworded text', async ({ language, width }) => {
    await inControls(language, width, wordsOf[language]['controls.presenterOff'], async () => undefined)
  })

  it.each(CASES)('shows the listening panel in $language at $width px without cut-off or unworded text', async ({ language, width }) => {
    await inControls(language, width, wordsOf[language]['controls.presenterOn'], async (page) => {
      await switchOn(page, language)
      await page.locator(`${CONTROLS_ROOT} .panel .heard`).waitFor({ state: 'visible' })
    })
  })

  it.each(CASES)('shows a fault with a number in $language at $width px without cut-off or unworded text', async ({ language, width }) => {
    state.answerMode = 'error-event'
    state.errorEvent = { reason: 'busy', retry_after_s: 7 }
    await inControls(language, width, wordsOf[language]['controls.busyIn']({ seconds: '7' }), async (page) => {
      await switchOn(page, language)
      const fault = page.locator(`${CONTROLS_ROOT} .fault`)
      await fault.waitFor({ state: 'visible' })
      expect(await fault.textContent()).toBe(wordsOf[language]['controls.busyIn']({ seconds: '7' }))
    })
  })

  // The room notice follows the deck's language, which the presenter's
  // account language differs from.
  const NOTICES = [
    { notice: 'band', projected: true, heardOnConnect: false },
    { notice: 'listening', projected: false, heardOnConnect: false },
    { notice: 'answering', projected: false, heardOnConnect: true },
  ]
  it.each(NOTICES.flatMap((notice) => CASES.map((shown) => ({ ...notice, ...shown }))))(
    'shows the $notice room notice in $language at $width px without cut-off or unworded text',
    async ({ notice, projected, heardOnConnect, language, width }) => {
      state.whoLanguage = language
      state.interfaceLanguage = otherLanguage(language)
      state.heardOnConnect = heardOnConnect
      const { browser, context } = await openRoomBrowser()
      try {
        const presenter = await openPageIn(context, `${state.origin}/presenter/1`, width)
        const projector = await openPageIn(context, `${state.origin}/1`, width)
        await switchOn(presenter, otherLanguage(language))
        const window = projected ? projector : presenter
        await window.locator(`[data-room-notice="${notice}"]`).waitFor({ state: 'visible', timeout: 15_000 })
        await expectRendersWell(window, NOTICE_ROOT, language, wordsOf[language][`roomNotice.${notice}`])
      } finally {
        await browser.close()
      }
    },
  )
})

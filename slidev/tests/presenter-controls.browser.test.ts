import { readFile, rm } from 'node:fs/promises'
import { join } from 'node:path'

import { HopClose } from 'overnightworks-speech-browser'
import { chromium, type Page } from 'playwright-chromium'
import { describe, expect, it } from 'vitest'

import {
  addonRootDirectory,
  boxesIntersect,
  buildAddonDeck,
  contrastRatio,
  createMediaObservation,
  createOverlayDeckServer,
  endHearings,
  expectFaultWordClearOfControls,
  expectPanelCoversNoSlidevControl,
  installCspGuard,
  openPageIn,
  openPresenter,
  openPresenterAt,
  openRoomBrowser,
  readBarBackground,
  readPaintedColours,
  readSameFrameBoxes,
  registerOverlayHarness,
  saveEvidence,
  sendHeard,
  state,
} from './presenter-overlay-harness'

registerOverlayHarness()

// Slidev's bottom navigation bar holds nothing of the overlay, and no part
// of the overlay reaches the lower half of the window.
async function expectNothingOfTheOverlayInTheBottomBar(page: Page, halfway: number) {
  const bottomBar = page.locator('.grid-section.bottom')
  expect(await bottomBar.locator('.copresenter, .presenter-bar, a[href="/"], [role="switch"]').count()).toBe(0)
  const lowestEdge = await page.evaluate(() => Math.max(...[...document.querySelectorAll('.presenter-bar, .presenter-bar *')]
    .map((element) => element.getBoundingClientRect().bottom)))
  expect(lowestEdge).toBeLessThanOrEqual(halfway)
}

describe('presenter controls', () => {
  it.each([1024, 390])('keeps the admitted sentence visible when speech is refused during a switch at %ipx', async (width) => {
    state.answerMode = 'speech-refused'
    state.askHeaderNames = []
    const { browser, page } = await openPresenter(width)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      const sentence = page.getByText('The handoff keeps this sentence.')
      const refusal = page.getByText('No answer')
      await sentence.waitFor({ state: 'visible', timeout: 5_000 })
      await refusal.waitFor({ state: 'visible', timeout: 5_000 })
      expect(state.askHeaderNames).toContain('content-type')
      expect(state.askHeaderNames).not.toContain('x-csrf-token')
      expect(await page.getByText('Not enabled').count()).toBe(0)
      expect(await sentence.isVisible()).toBe(true)
      expect(await refusal.isVisible()).toBe(true)
      await saveEvidence(page, `presenter-speech-refused-${width}.png`)
      await ai.click({ timeout: 2_000 })
      await expect.poll(() => state.hearingClosed).toBeGreaterThanOrEqual(state.hearingOpened)
    } finally {
      state.answerMode = 'audio'
      await browser.close()
    }
  })

  it('Counts only a voiced sentence as spoken when an answer fails midway', async () => {
    state.answerMode = 'mid-answer-error'
    const { browser, page } = await openPresenter(1024)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await page.getByText('No answer', { exact: true }).waitFor({ state: 'visible', timeout: 5_000 })
      expect(await page.getByText('Then the second one fails.').count()).toBeGreaterThan(0)
      // The fake player never ends on its own; ending it lets the next question be heard.
      type Snapshot = { speaking: boolean }
      await page.waitForFunction(() => (window as unknown as {
        __copresenter: { snapshot: () => Snapshot }
      }).__copresenter.snapshot().speaking)
      await page.evaluate(() => (window as unknown as { __fakes: { endPlayback: () => void } }).__fakes.endPlayback())
      await page.waitForFunction(() => !(window as unknown as {
        __copresenter: { snapshot: () => Snapshot }
      }).__copresenter.snapshot().speaking)
      sendHeard('The first sentence is voiced.')
      sendHeard('Then the second one fails.')
      await expect.poll(() => state.askBodies.length).toBe(2)
      expect(state.askBodies.map((body) => body.said)).toEqual([
        'Where does the handoff slow down?',
        'Then the second one fails.',
      ])
    } finally {
      state.answerMode = 'audio'
      await browser.close()
    }
  }, 30_000)

  it.each([1024, 390])('CP15: puts Home at the left and the AI switch at the right of one bar above the sections at %ipx', async (width) => {
    const { browser, page } = await openPresenter(width)
    try {
      const home = page.getByRole('link', { name: 'Home' })
      const ai = page.getByRole('switch', { name: 'AI' })
      await home.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.waitFor({ state: 'visible' })
      const { homeBox, aiBox, barBox, sectionsBox } = await readSameFrameBoxes(page, await home.elementHandle(), await ai.elementHandle())
      expect(homeBox.width).toBeGreaterThanOrEqual(44)
      expect(homeBox.height).toBeGreaterThanOrEqual(44)
      expect(aiBox.width).toBeGreaterThanOrEqual(44)
      expect(aiBox.height).toBeGreaterThanOrEqual(44)
      expect((await home.innerText()).trim()).toBe('')
      expect(await home.getAttribute('title')).toBe('Home')
      expect(homeBox.x - barBox.x, 'Home leads the bar').toBeLessThan(24)
      expect(barBox.x + barBox.width - (aiBox.x + aiBox.width), 'the switch ends the bar').toBeLessThan(24)
      expect(boxesIntersect(homeBox, aiBox)).toBe(false)
      expect(barBox.y + barBox.height, 'the bar sits above the sections').toBeLessThanOrEqual(sectionsBox.y + 1)
      const slidevControls = [
        page.getByText('Current', { exact: true }),
        page.getByText('Screen Mirror', { exact: true }),
      ]
      for (const control of slidevControls) {
        const box = (await control.boundingBox())!
        expect(boxesIntersect(box, homeBox)).toBe(false)
        expect(boxesIntersect(box, aiBox)).toBe(false)
      }
      await saveEvidence(page, `presenter-home-${width}.png`)

      await home.focus()
      await page.keyboard.press('Enter')
      await page.waitForURL(`${state.origin}/`)
    } finally {
      await browser.close()
    }
  })

  it.each([1, 2, 3].flatMap((layout) => [1440, 1024, 390].map((width) => [layout, width] as const)))('CP15: keeps the bar above every section of Slidev presenter layout %i at %ipx and its open panel covers no Slidev control', async (layout, width) => {
    const { browser, page } = await openPresenterAt(state.origin, '/presenter/1', width, async (prepared) => {
      await prepared.addInitScript((chosen) => localStorage.setItem('slidev-presenter-layout', String(chosen)), layout)
    })
    try {
      const home = page.getByRole('link', { name: 'Home' })
      const ai = page.getByRole('switch', { name: 'AI' })
      await home.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await page.locator('.copresenter .panel').waitFor({ state: 'visible', timeout: 5_000 })
      const { homeBox, aiBox, barBox, sectionsBox } = await readSameFrameBoxes(page, await home.elementHandle(), await ai.elementHandle())
      expect(await page.locator('.grid-container').getAttribute('class')).toContain(`layout${layout}`)
      expect(barBox.y + barBox.height).toBeLessThanOrEqual(sectionsBox.y + 1)
      expect(homeBox.x - barBox.x).toBeLessThan(24)
      expect(barBox.x + barBox.width - (aiBox.x + aiBox.width)).toBeLessThan(24)
      for (const control of [page.getByText('Current', { exact: true }), page.getByText('Screen Mirror', { exact: true })]) {
        const box = (await control.boundingBox())!
        expect(boxesIntersect(box, homeBox)).toBe(false)
        expect(boxesIntersect(box, aiBox)).toBe(false)
      }
      await expectPanelCoversNoSlidevControl(page)
      await saveEvidence(page, `presenter-layout-${layout}-${width}-heard-panel.png`)
    } finally {
      await browser.close()
    }
  })

  it('CP15: paints the track grey off and green on, with the word AI and no state word', async () => {
    state.heardOnConnect = false
    const { browser, page } = await openPresenter(1024)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      const bar = page.locator('.presenter-bar')
      expect(await ai.getAttribute('aria-checked')).toBe('false')
      expect(await ai.innerText()).toBe('AI')
      const [red, green, blue] = (await readPaintedColours(page)).track
      expect(Math.max(red, green, blue) - Math.min(red, green, blue), 'off is grey').toBeLessThan(24)
      await ai.click({ timeout: 2_000 })
      await expect.poll(async () => (await readPaintedColours(page)).track[1]).toBeGreaterThan(120)
      const [onRed, onGreen, onBlue] = (await readPaintedColours(page)).track
      expect(onGreen, 'on is green').toBeGreaterThan(Math.max(onRed, onBlue) + 20)
      expect(await ai.getAttribute('aria-checked')).toBe('true')
      await expect.poll(async () => (await page.evaluate(() => (window as unknown as {
        __copresenter: { snapshot: () => { capturing: boolean } }
      }).__copresenter.snapshot().capturing))).toBe(true)
      expect(await ai.innerText()).toBe('AI')
      expect((await bar.innerText()).trim()).toBe('AI')
      expect(await page.getByText('local', { exact: false }).count()).toBe(0)
      await saveEvidence(page, 'presenter-on-1024.png')
    } finally {
      state.heardOnConnect = true
      await browser.close()
    }
  })

  it.each(['light', 'dark'] as const)('CP15: draws the bar flat, with an off track clear of the bar and a focus ring on Home and the switch in the %s scheme', async (scheme) => {
    const { browser, page } = await openPresenter(1024, scheme)
    try {
      const home = page.getByRole('link', { name: 'Home' })
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      const flat = await ai.evaluate((element) => {
        const style = getComputedStyle(element)
        return { border: style.borderTopWidth, shadow: style.boxShadow, background: style.backgroundColor }
      })
      expect(flat.border, 'no border').toBe('0px')
      expect(flat.shadow, 'no shadow').toBe('none')
      expect(flat.background, 'no box').toMatch(/^rgba\(.*, 0\)$|^transparent$/)
      expect(contrastRatio((await readPaintedColours(page)).track, await readBarBackground(page)), 'the off track stands out').toBeGreaterThanOrEqual(3)
      const expectFocusRing = async (locator: typeof home, name: string) => {
        const ring = await locator.evaluate((element) => {
          const style = getComputedStyle(element)
          return { style: style.outlineStyle, width: parseFloat(style.outlineWidth) }
        })
        expect(ring.style, `${name} shows focus`).toBe('solid')
        expect(ring.width, `${name}'s ring is thick enough to see`).toBeGreaterThanOrEqual(2)
      }
      await home.focus()
      await expectFocusRing(home, 'Home')
      await page.keyboard.press('Tab')
      await expect.poll(() => ai.evaluate((element) => element === document.activeElement)).toBe(true)
      await expectFocusRing(ai, 'the switch')
      await saveEvidence(page, `presenter-bar-focus-${scheme}-1024.png`)
    } finally {
      await browser.close()
    }
  })

  it.each(['light', 'dark'] as const)('CP15: sets the heard panel on the bar in upright text at the bar\'s size, readable in the %s scheme', async (scheme) => {
    state.answerMode = 'mid-answer-error'
    const { browser, page } = await openPresenter(1024, scheme)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await page.locator('.copresenter .panel .answer').waitFor({ state: 'visible', timeout: 5_000 })
      const background = await readBarBackground(page)
      const lines = await page.locator('.copresenter .panel .line').evaluateAll((elements) => elements.map((element) => {
        const style = getComputedStyle(element)
        return { color: style.color, opacity: Number(style.opacity), fontStyle: style.fontStyle, fontSize: style.fontSize }
      }))
      const barFontSize = await ai.evaluate((element) => getComputedStyle(element).fontSize)
      expect(lines).toHaveLength(2)
      for (const line of lines) {
        expect(line.fontStyle, 'upright').toBe('normal')
        expect(line.fontSize, 'the bar\'s size').toBe(barFontSize)
        const colour = line.color.match(/[\d.]+/g)!.slice(0, 3).map(Number) as [number, number, number]
        expect(contrastRatio(colour, background, line.opacity), 'readable').toBeGreaterThanOrEqual(4.5)
      }
      const surface = await page.locator('.copresenter .panel').evaluate((element) => {
        const style = getComputedStyle(element)
        return { border: style.borderTopWidth, shadow: style.boxShadow, background: style.backgroundColor }
      })
      expect(surface.border, 'a hairline border').toBe('1px')
      expect(surface.shadow, 'no shadow').toBe('none')
      expect(surface.background, 'the bar\'s own surface').toMatch(/^rgba\(.*, 0\)$|^transparent$/)
      await saveEvidence(page, `presenter-heard-panel-${scheme}-1024.png`)
    } finally {
      state.answerMode = 'audio'
      await browser.close()
    }
  })

  it('CP15: stops the active co-presenter before Home leaves, keeps the overlay out of the bottom and a return starts off', async () => {
    state.media = createMediaObservation()
    const { browser, page } = await openPresenter(390)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await expect.poll(() => page.evaluate(() => (window as unknown as {
        __copresenter: { snapshot: () => { speaking: boolean } }
      }).__copresenter.snapshot().speaking), { timeout: 5_000 }).toBe(true)
      await page.getByText('Where does the handoff slow down?').waitFor({ state: 'visible', timeout: 5_000 })
      expect(state.media.trackStopped).toBe(false)
      expect(state.media.playbackStarted).toBe(true)
      expect(state.media.playbackPaused).toBe(false)
      const [homeBox, aiBox, panelBox] = await Promise.all([
        page.getByRole('link', { name: 'Home' }).boundingBox(),
        ai.boundingBox(),
        page.locator('.copresenter .panel').boundingBox(),
      ])
      expect(homeBox).not.toBeNull()
      expect(aiBox).not.toBeNull()
      expect(panelBox).not.toBeNull()
      expect(panelBox!.width).toBeLessThanOrEqual(390 - 16)
      expect(panelBox!.height).toBeLessThanOrEqual(900 * 0.42)
      expect(boxesIntersect(homeBox!, panelBox!)).toBe(false)
      expect(boxesIntersect(aiBox!, panelBox!)).toBe(false)
      expect(panelBox!.y, 'the panel opens below the switch').toBeGreaterThanOrEqual(aiBox!.y + aiBox!.height)
      expect(panelBox!.x).toBeGreaterThanOrEqual(0)
      expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(390)
      expect(panelBox!.y + panelBox!.height).toBeLessThanOrEqual(900)
      await expectPanelCoversNoSlidevControl(page)
      await expectNothingOfTheOverlayInTheBottomBar(page, 900 / 2)
      await saveEvidence(page, 'presenter-active-390.png')
      await page.getByRole('link', { name: 'Home' }).click({ timeout: 2_000 })
      await page.waitForURL(`${state.origin}/`)
      await expect.poll(() => state.hearingClosed).toBeGreaterThanOrEqual(state.hearingOpened)
      await expect.poll(() => state.pendingAnswerClosed).toBeGreaterThan(0)
      await expect.poll(() => state.media.trackStopped).toBe(true)
      await expect.poll(() => state.media.playbackPaused).toBe(true)

      await page.goto(`${state.origin}/presenter/1`, { waitUntil: 'domcontentloaded', timeout: 10_000 })
      expect(await page.getByRole('switch', { name: 'AI' }).getAttribute('aria-checked')).toBe('false')
    } finally {
      await browser.close()
    }
  })

  it.each(([[1024, 'light'], [390, 'light'], [1024, 'dark']] as const).flatMap(([width, scheme]) => ([
    ['speech cannot be reached', 'unreachable', 'Voice offline'],
    ['the co-presenter fails without being offline', 'who-fails', 'Unreachable'],
    ['the deck is not the asker\'s', 'who-not-found', 'Unreachable'],
    ['local hearing is not ready', 'not-ready', 'Local hearing not ready'],
    ['the server refuses the hearing', 'refused-on-open', 'No hearing'],
    ['the hearing fails after capture started', 'fails-after-capture', 'No hearing'],
  ] as const).map((row) => [width, scheme, ...row] as const)))('CP7: says so in a word readable on the bar, clear of Home and the switch, keeps the track green and builds no browser speech recognition at %ipx in the %s scheme when %s', async (width, scheme, _situation, mode, message) => {
    state.hearingMode = mode
    state.media = createMediaObservation()
    const hearingOpenedBefore = state.hearingOpened
    const { browser, page } = await openPresenter(width, scheme)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      if (mode === 'fails-after-capture') {
        // The end must land after capture is live, so it proves the end of a
        // running capture releases the microphone.
        await expect.poll(() => page.evaluate(
          () => (window as unknown as {
            __copresenter: { snapshot: () => { capturing: boolean } }
          }).__copresenter.snapshot().capturing,
        )).toBe(true)
        endHearings(HopClose.SERVICE_FAILED)
      }
      await page.getByText(message).waitFor({ state: 'visible', timeout: 5_000 })
      await expectFaultWordClearOfControls(page, width, ai, page.getByText(message))
      const painted = await readPaintedColours(page)
      expect(contrastRatio(painted.fault!, await readBarBackground(page)), 'the word reads on the bar').toBeGreaterThanOrEqual(4.5)
      expect(painted.fault, 'a warning colour, not the bar\'s own text colour').not.toEqual(painted.barText)
      expect(painted.track[1], 'the track stays green').toBeGreaterThan(Math.max(painted.track[0], painted.track[2]) + 20)
      expect(await page.evaluate(
        () => (window as unknown as { __speechRecognitionsBuilt: number }).__speechRecognitionsBuilt,
      )).toBe(0)
      await expect.poll(() => state.hearingClosed).toBeGreaterThanOrEqual(state.hearingOpened)
      if (['unreachable', 'who-fails', 'who-not-found'].includes(mode)) expect(state.hearingOpened).toBe(hearingOpenedBefore)
      if (['refused-on-open', 'fails-after-capture'].includes(mode)) await expect.poll(() => state.media.trackStopped).toBe(true)
      else {
        expect(await page.evaluate(
          () => (window as unknown as { __microphonesRequested: number }).__microphonesRequested,
        )).toBe(0)
      }
    } finally {
      state.hearingMode = 'ready'
      await browser.close()
    }
  })

  it('CP14: shows no fault word when the server ends the hearing in order, and lets the microphone go', async () => {
    state.media = createMediaObservation()
    const { browser, page } = await openPresenter(1024)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      const capturing = () => page.evaluate(() => (window as unknown as {
        __copresenter: { snapshot: () => { capturing: boolean } }
      }).__copresenter.snapshot().capturing)
      await expect.poll(capturing).toBe(true)
      endHearings(HopClose.NORMAL)
      await expect.poll(capturing, { timeout: 1_000 }).toBe(false)
      await expect.poll(() => state.media.trackStopped, { timeout: 1_000 }).toBe(true)
      for (const word of ['No hearing', 'Unreachable', 'Voice offline', 'Local hearing not ready']) {
        expect(await page.getByText(word, { exact: true }).count()).toBe(0)
      }
    } finally {
      await browser.close()
    }
  })

  it.each(['de', 'en'] as const)('CP10: the answered language %s reaches /hear and /ask', async (language) => {
    state.whoLanguage = language
    const { browser, page } = await openPresenter(1024)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await expect.poll(() => state.askBodies.length).toBeGreaterThan(0)
      expect(state.hearLanguages).toEqual([language])
      expect(state.askBodies[0]?.language).toBe(language)
      expect(await ai.textContent()).toBe('AI')
    } finally {
      state.whoLanguage = 'de'
      await browser.close()
    }
  })

  it.each([
    { width: 1024, shown: 3, followed: ['/presenter/3', '/3'] },
    { width: 390, shown: 3, followed: ['/presenter/3', '/3'] },
    { width: 1024, shown: 9, followed: ['/presenter/1', '/1'] },
    { width: 390, shown: 0, followed: ['/presenter/1', '/1'] },
    { width: 390, shown: 1.5, followed: ['/presenter/1', '/1'] },
  ])('lets a slide event move the presenter and projector only inside the deck: $shown at $width px', async ({ width, shown, followed }) => {
    state.answerMode = 'moves-slide'
    state.slideToShow = shown
    state.askBodies = []
    const { browser, context } = await openRoomBrowser()
    try {
      const presenter = await openPageIn(context, `${state.origin}/presenter/1`, width)
      const projector = await openPageIn(context, `${state.origin}/1`, width)
      await presenter.getByRole('switch', { name: 'AI' }).click({ timeout: 2_000 })
      await presenter.getByText('Answered.', { exact: true }).waitFor({ state: 'visible', timeout: 5_000 })
      await expect.poll(() => new URL(presenter.url()).pathname).toBe(followed[0])
      await expect.poll(() => new URL(projector.url()).pathname).toBe(followed[1])
      // The event adds no line of its own: only the heard question and the answer show.
      expect(await presenter.locator('.copresenter .line').allTextContents()).toEqual([
        'Where does the handoff slow down?',
        'Answered.',
      ])
      await saveEvidence(presenter, `presenter-slide-${shown}-${width}.png`)
    } finally {
      state.answerMode = 'audio'
      await browser.close()
    }
  })

  it('asks the next question with the slide the model moved to', async () => {
    state.answerMode = 'moves-slide'
    state.slideToShow = 2
    state.askBodies = []
    const { browser, page } = await openPresenter(1024)
    try {
      await page.getByRole('switch', { name: 'AI' }).click({ timeout: 2_000 })
      await expect.poll(() => new URL(page.url()).pathname).toBe('/presenter/2')
      await page.getByText('Answered.', { exact: true }).waitFor({ state: 'visible', timeout: 5_000 })
      sendHeard('And now?')
      await expect.poll(() => state.askBodies.length).toBe(2)
      expect(state.askBodies.map((body) => body.slide)).toEqual([1, 2])
    } finally {
      state.answerMode = 'audio'
      await browser.close()
    }
  })

  it('does not put presenter controls on the projector', async () => {
    const browser = await chromium.launch({ headless: true })
    const page = await browser.newPage({ viewport: { width: 1024, height: 900 } })
    page.setDefaultTimeout(5_000)
    await installCspGuard(page)
    try {
      await page.goto(`${state.origin}/1`, { waitUntil: 'domcontentloaded', timeout: 10_000 })
      expect(await page.getByRole('link', { name: 'Home' }).count()).toBe(0)
      expect(await page.getByRole('switch', { name: 'AI' }).count()).toBe(0)
      expect(await page.locator('.presenter-bar, .copresenter').count()).toBe(0)
      await saveEvidence(page, 'projector.png')
    } finally {
      await browser.close()
    }
  })

  // The overlay sends no token; the browser's own Origin vouches for the ask.
  it('reads no cookie: the browser vouches for the ask with its own Origin', async () => {
    const overlay = await readFile(join(addonRootDirectory, 'components/CoPresenter.vue'), 'utf8')
    expect(overlay).not.toContain('document.cookie')
  })

  it.each([
    ['/', null],
    ['/deck/50%25off/', '50%off'],
    ['/deck/%C3%BC%20b%2541/', 'ü b%41'],
  ])('mounts one overlay and loads the capture worklet from <base>assets/ at %s', async (base, slug) => {
    const nested = slug !== null
    const overlayBuildDirectory = await buildAddonDeck(base)
    const asked: { said: string, deck: string }[] = []
    const whoDecks: (string | null)[] = []
    const { server: overlayServer, origin: overlayOrigin, sockets: overlaySockets }
      = await createOverlayDeckServer(overlayBuildDirectory, {
        basePath: nested ? base.slice(0, -1) : '',
        onAsk: (said, deck) => asked.push({ said, deck }),
        onWho: (deck) => whoDecks.push(deck),
      })
    try {
      const { browser, page } = await openPresenterAt(overlayOrigin, `${base}presenter/1`, 1024)
      try {
        const ai = page.getByRole('switch', { name: 'AI' })
        await ai.waitFor({ state: 'visible', timeout: 2_000 })
        expect(await page.getByRole('switch', { name: 'AI' }).count()).toBe(1)
        expect(await page.getByRole('link', { name: 'Home' }).count()).toBe(1)
        await ai.click({ timeout: 2_000 })
        // `capturing` (snapshot()) is true only once the package's worklet node
        // runs, which happens after `addModule` resolved against the worklet
        // emitted under `<base>assets/`, under the production CSP; a blocked
        // or missing module ends the capture instead and the component says
        // it has no hearing.
        await page.waitForFunction(
          () => (window as unknown as {
            __copresenter: { snapshot: () => { capturing: boolean } }
          }).__copresenter.snapshot().capturing,
          undefined,
          { timeout: 5_000 },
        )
        expect(await page.getByText('No hearing').count()).toBe(0)
        if (nested) {
          await page.evaluate(() => (window as unknown as {
            __copresenter: { say: (text: string) => void }
          }).__copresenter.say('Which deck is this?'))
          await expect.poll(() => asked).toEqual([{ said: 'Which deck is this?', deck: slug }])
          expect(whoDecks).toEqual([slug])
        }
      } finally {
        await browser.close()
      }
    } finally {
      for (const socket of overlaySockets) socket.destroy()
      overlayServer.closeAllConnections()
      await new Promise<void>((resolve, reject) => {
        overlayServer.close((error) => (error ? reject(error) : resolve()))
      })
      await rm(overlayBuildDirectory, { recursive: true, force: true })
    }
  }, 60_000)
})

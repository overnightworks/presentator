import { readFile, rm } from 'node:fs/promises'
import { join } from 'node:path'

import { chromium } from 'playwright-chromium'
import { describe, expect, it } from 'vitest'

import {
  addonRootDirectory,
  boxesIntersect,
  buildAddonDeck,
  createMediaObservation,
  createOverlayDeckServer,
  installCspGuard,
  openPresenter,
  openPresenterAt,
  readSameFrameBoxes,
  registerOverlayHarness,
  saveEvidence,
  sendHeard,
  sendHearingError,
  state,
} from './presenter-overlay-harness'

registerOverlayHarness()

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

  it('Cell 9: counts only a voiced sentence as spoken when an answer fails midway', async () => {
    state.answerMode = 'mid-answer-error'
    const { browser, page } = await openPresenterAt(state.origin, '/presenter/1', 1024, async (prepared) => {
      await prepared.addInitScript(() => {
        const RealAudio = window.Audio
        const made: HTMLAudioElement[] = []
        ;(window as unknown as { __audios: HTMLAudioElement[] }).__audios = made
        window.Audio = function (url?: string) {
          const audio = new RealAudio(url)
          made.push(audio)
          return audio
        } as unknown as typeof Audio
      })
    })
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
      await page.evaluate(() => (window as unknown as { __audios: HTMLAudioElement[] }).__audios
        .forEach((audio) => audio.dispatchEvent(new Event('ended'))))
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

  it.each([1024, 390])('keeps Home and AI separate and reachable at %ipx', async (width) => {
    const { browser, page } = await openPresenter(width)
    try {
      const home = page.getByRole('link', { name: 'Home' })
      const ai = page.getByRole('switch', { name: 'AI' })
      await home.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.waitFor({ state: 'visible' })
      const { homeBox, aiBox, navWidth } = await readSameFrameBoxes(page, await home.elementHandle(), await ai.elementHandle())
      expect(homeBox.width).toBeGreaterThanOrEqual(44)
      expect(homeBox.height).toBeGreaterThanOrEqual(44)
      expect(aiBox.width).toBeGreaterThanOrEqual(44)
      expect(aiBox.height).toBeGreaterThanOrEqual(44)
      expect(
        boxesIntersect(homeBox, aiBox),
        `Home ${JSON.stringify(homeBox)} overlaps AI ${JSON.stringify(aiBox)} in a nav row ${navWidth}px wide`,
      ).toBe(false)
      await saveEvidence(page, `presenter-home-${width}.png`)

      await home.focus()
      await page.keyboard.press('Enter')
      await page.waitForURL(`${state.origin}/`)
    } finally {
      await browser.close()
    }
  })

  it('stops the active co-presenter before Home leaves and direct return starts off', async () => {
    state.media = createMediaObservation()
    const { browser, page } = await openPresenter(390)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await page.locator('.copresenter[data-state="speak"]').waitFor({ state: 'visible', timeout: 5_000 })
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

  it.each([1024, 390].flatMap((width) => ([
    ['#512 line 2: speech cannot be reached', 'unreachable', 'Voice offline'],
    ['#512 line 2: the co-presenter fails without being offline', 'who-fails', 'Unreachable'],
    ['#615 cell 21: the deck is not the asker\'s', 'who-not-found', 'Unreachable'],
    ['local hearing is not ready', 'not-ready', 'Local hearing not ready'],
    ['the hearing socket sends an error', 'socket-error', 'No hearing'],
  ] as const).map((row) => [width, ...row] as const)))('says so and builds no browser speech recognition at %ipx when %s', async (width, _situation, mode, message) => {
    state.hearingMode = mode
    state.media = createMediaObservation()
    const hearingOpenedBefore = state.hearingOpened
    const { browser, page } = await openPresenter(width)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      if (mode === 'socket-error') {
        // The error must land after activation finished: an error during
        // `addModule` is released by activation's own cleanup and would not
        // prove the error path releases the microphone.
        await expect.poll(() => page.evaluate(
          () => (window as unknown as {
            __copresenter: { snapshot: () => { workletLive: boolean } }
          }).__copresenter.snapshot().workletLive,
        )).toBe(true)
        sendHearingError()
      }
      await page.getByText(message).waitFor({ state: 'visible', timeout: 5_000 })
      expect(await page.locator('.copresenter').getAttribute('data-hearing')).toBe('off')
      expect(await page.evaluate(
        () => (window as unknown as { __speechRecognitionsBuilt: number }).__speechRecognitionsBuilt,
      )).toBe(0)
      await expect.poll(() => state.hearingClosed).toBeGreaterThanOrEqual(state.hearingOpened)
      if (['unreachable', 'who-fails', 'who-not-found'].includes(mode)) expect(state.hearingOpened).toBe(hearingOpenedBefore)
      if (mode === 'socket-error') await expect.poll(() => state.media.trackStopped).toBe(true)
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

  it.each(['de', 'en'] as const)('#615 cells 18, 19: the answered language %s reaches /hear and /ask', async (language) => {
    state.whoLanguage = language
    const { browser, page } = await openPresenter(1024)
    try {
      const ai = page.getByRole('switch', { name: 'AI' })
      await ai.waitFor({ state: 'visible', timeout: 2_000 })
      await ai.click({ timeout: 2_000 })
      await expect.poll(() => state.askBodies.length).toBeGreaterThan(0)
      expect(state.hearLanguages).toEqual([language])
      expect(state.askBodies[0]?.language).toBe(language)
      expect(await ai.textContent()).toContain('AI')
    } finally {
      state.whoLanguage = 'de'
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
      await saveEvidence(page, 'projector.png')
    } finally {
      await browser.close()
    }
  })

  // #474 cell 35: the overlay sends no token; the browser's own Origin vouches for the ask.
  it('reads no cookie: the browser vouches for the ask with its own Origin', async () => {
    const overlay = await readFile(join(addonRootDirectory, 'components/CoPresenter.vue'), 'utf8')
    expect(overlay).not.toContain('document.cookie')
  })

  it.each([
    ['/', null],
    ['/deck/50%25off/', '50%off'],
    ['/deck/%C3%BC%20b%2541/', 'ü b%41'],
  ])('mounts one overlay and loads its AudioWorklet from <base>assets/ at %s', async (base, slug) => {
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
        // `hearing` reaches 'local' optimistically, before
        // `AudioContext.audioWorklet.addModule` - real, not faked
        // (installMediaFakes) - is even awaited (CoPresenter.vue), so a test
        // that only waits for that attribute can pass while the worklet is
        // still loading or a CSP block is still in flight. `workletLive`
        // (snapshot()) is true only once the worklet node actually exists,
        // which happens after `addModule` resolved against the addon's
        // own worklet, emitted under `<base>assets/`, under the
        // production CSP; a blocked or missing module throws there instead
        // and the component says it has no hearing.
        await page.waitForFunction(
          () => (window as unknown as {
            __copresenter: { snapshot: () => { workletLive: boolean } }
          }).__copresenter.snapshot().workletLive,
          undefined,
          { timeout: 5_000 },
        )
        const snapshot = await page.evaluate(
          () => (window as unknown as {
            __copresenter: { snapshot: () => { hearing: string } }
          }).__copresenter.snapshot(),
        )
        expect(snapshot.hearing).toBe('local')
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

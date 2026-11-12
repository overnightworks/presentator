import { rm } from 'node:fs/promises'

import { HopClose } from 'overnightworks-speech-browser'
import type { BrowserContext, Page } from 'playwright-chromium'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  buildAddonDeck,
  createOverlayDeckServer,
  endHearings,
  freezePage,
  openPageIn,
  openRoomBrowser,
  registerOverlayHarness,
  saveEvidence,
  sendHeard,
  state,
} from './presenter-overlay-harness'

registerOverlayHarness()
vi.setConfig({ testTimeout: 90_000 })

// CP11: the ruled sentences, and the pill words, as scenario data.
const BANNER = {
  de: 'Dieser Vortrag nutzt einen KI-Co-Presenter. Solange er eingeschaltet ist, wird Gesprochenes lokal in Text umgewandelt und mit den Folien an einen KI-Dienst gesendet. Welcher, steht in der Datenschutzerklärung.',
  en: 'This talk uses an AI co-presenter. While it is switched on, speech is converted to text locally and sent with the slides to an AI service. The Datenschutzerklärung (privacy notice) says which one.',
}
const PILL = {
  de: { listening: 'KI hört zu', answering: 'KI antwortet' },
  en: { listening: 'AI is listening', answering: 'AI is answering' },
}

type Fakes = {
  microphone: 'ready' | 'pending' | 'denied'
  worklet: 'ready' | 'failing'
  playback: 'resolve' | 'pending' | 'rejected' | 'second-fails-on-cue'
  failSecond: (() => void) | null
  releaseMicrophone: () => void
  endPlayback: () => void
}
type Notice = { phase: string, text: string } | null
type TimelineEntry = { at: number, phase: string | null }
type Overlay = { snapshot: () => { capturing: boolean, speaking: boolean, on: boolean }, say: (text: string) => void }
type Probed = Window & { __fakes: Fakes, __copresenter: Overlay, __timeline: TimelineEntry[], __roomMessages: { phase: string, at: number }[] }

const scenarioDefaults = {
  answerMode: state.answerMode,
  whoLanguage: state.whoLanguage,
  hearingMode: state.hearingMode,
  heardOnConnect: state.heardOnConnect,
}
afterEach(() => { Object.assign(state, scenarioDefaults) })

const setFakes = (page: Page, fakes: Partial<Pick<Fakes, 'microphone' | 'worklet' | 'playback'>>) =>
  page.evaluate((patch) => Object.assign((window as unknown as Probed).__fakes, patch), fakes)
const callFake = (page: Page, name: 'releaseMicrophone' | 'endPlayback') =>
  page.evaluate((fake) => (window as unknown as Probed).__fakes[fake](), name)
const snapshot = (page: Page) => page.evaluate(() => (window as unknown as Probed).__copresenter.snapshot())

const readNotice = (page: Page): Promise<Notice> => page.evaluate(() => {
  const element = document.querySelector('[data-room-notice]')
  return element ? { phase: element.getAttribute('data-room-notice')!, text: element.textContent! } : null
})

// Records every change of the notice in the DOM, so a notice that shows for
// one frame is seen where a 50 ms poll would miss it.
async function recordNotice(page: Page) {
  await page.evaluate(() => {
    const timeline: TimelineEntry[] = []
    const note = () => {
      const phase = document.querySelector('[data-room-notice]')?.getAttribute('data-room-notice') ?? null
      if (timeline.at(-1)?.phase !== phase) timeline.push({ at: Date.now(), phase })
    }
    note()
    new MutationObserver(note).observe(document.documentElement, { childList: true, subtree: true, attributes: true })
    ;(window as unknown as Probed).__timeline = timeline
  })
}
const timelineOf = (page: Page) => page.evaluate(() => (window as unknown as Probed).__timeline)
const markTimeline = async (page: Page) => (await timelineOf(page)).length
async function phasesSince(page: Page, mark: number) {
  return (await timelineOf(page)).slice(mark).map((entry) => entry.phase).filter((phase) => phase !== null)
}

type Room = { context: BrowserContext, presenter: Page, projector: Page }

async function inRoom(run: (room: Room) => Promise<void>, { colorScheme = 'light' as 'light' | 'dark', presenterWidth = 1024 } = {}) {
  const { browser, context } = await openRoomBrowser(colorScheme)
  try {
    const presenter = await openPageIn(context, `${state.origin}/presenter/1`, presenterWidth)
    const projector = await openPageIn(context, `${state.origin}/1`, 1024)
    await recordNotice(presenter)
    await recordNotice(projector)
    await run({ context, presenter, projector })
  } finally {
    await browser.close()
  }
}

async function recordedProjector(context: BrowserContext) {
  const projector = await openPageIn(context, `${state.origin}/1`, 1024)
  await recordNotice(projector)
  return projector
}

async function switchOn(presenter: Page) {
  const ai = presenter.getByRole('switch', { name: 'AI' })
  await ai.waitFor({ state: 'visible', timeout: 2_000 })
  await ai.click({ timeout: 2_000 })
}
const waitForCapture = (presenter: Page) =>
  expect.poll(async () => (await snapshot(presenter)).capturing, { timeout: 5_000 }).toBe(true)
// CP9: the band lasts ten seconds on a window, then the pill takes over.
const BAND_SECONDS = 10
const showsPhase = (page: Page, phase: string, text: string, timeout = 15_000) =>
  expect.poll(() => readNotice(page), { timeout, interval: 50 }).toEqual({ phase, text })
const showsBand = (page: Page, language: 'de' | 'en' = 'de', timeout = 5_000) =>
  showsPhase(page, 'band', BANNER[language], timeout)
const isEmpty = (page: Page, timeout: number) =>
  expect.poll(() => readNotice(page), { timeout, interval: 50 }).toBeNull()

// What a window showed from its first notice on, a gap shown as null.
async function shownSinceFirstNotice(page: Page, from = 0) {
  const entries = (await timelineOf(page)).slice(from)
  return entries.slice(entries.findIndex((entry) => entry.phase !== null)).map((entry) => entry.phase)
}
const bandStartedAt = async (page: Page) => (await timelineOf(page)).find((entry) => entry.phase === 'band')!.at
const phaseStartedAt = async (page: Page, phase: string) => (await timelineOf(page)).find((entry) => entry.phase === phase)!.at

async function expectNothingFor(pages: Page[], milliseconds: number) {
  await pages[0]!.waitForTimeout(milliseconds)
  for (const page of pages) expect(await phasesSince(page, 0)).toEqual([])
}

async function clearanceOf(pages: Page[], label: string) {
  const started = Date.now()
  for (const page of pages) await isEmpty(page, 2_500)
  const elapsed = Date.now() - started
  console.info(`${label}: both windows empty after ${elapsed} ms`)
  return elapsed
}

describe('the room notice', () => {
  it.each(['de', 'en'] as const)(
    'CP9: the band for ten seconds, then the listening pill, then the answering pill in %s',
    async (language) => {
      state.whoLanguage = language
      state.heardOnConnect = false
      await inRoom(async ({ presenter, projector }) => {
        await switchOn(presenter)
        await waitForCapture(presenter)
        await showsBand(projector, language)
        await showsPhase(presenter, 'listening', PILL[language].listening)
        await saveEvidence(projector, `room-band-${language}-1024.png`)
        await saveEvidence(presenter, `room-presenter-listening-${language}.png`)
        await showsPhase(projector, 'listening', PILL[language].listening)
        const lasted = (await phaseStartedAt(projector, 'listening')) - (await bandStartedAt(projector))
        console.info(`band on the projector lasted ${lasted} ms in ${language}`)
        expect(lasted).toBeGreaterThanOrEqual(BAND_SECONDS * 1000 - 200)
        expect(lasted).toBeLessThanOrEqual(BAND_SECONDS * 1000 + 1_500)
        expect(await shownSinceFirstNotice(projector)).toEqual(['band', 'listening'])
        expect(await shownSinceFirstNotice(presenter)).toEqual(['listening'])
        await saveEvidence(projector, `room-listening-${language}-1024.png`)
        sendHeard('Where does the handoff slow down?')
        await showsPhase(projector, 'answering', PILL[language].answering)
        await showsPhase(presenter, 'answering', PILL[language].answering)
        await saveEvidence(projector, `room-answering-${language}-1024.png`)
        await callFake(presenter, 'endPlayback')
        await showsPhase(projector, 'listening', PILL[language].listening)
        await showsPhase(presenter, 'listening', PILL[language].listening)
        expect(await presenter.getByRole('switch', { name: 'AI' }).textContent()).toBe('AI')
      })
    },
  )

  it('CP9: holds the band to its end when an answer plays during it', async () => {
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(presenter, 'answering', PILL.de.answering)
      await showsBand(projector)
      const answeringSince = Date.now()
      await showsPhase(projector, 'answering', PILL.de.answering)
      const bandEnd = await phaseStartedAt(projector, 'answering')
      expect(bandEnd - (await bandStartedAt(projector))).toBeGreaterThanOrEqual(BAND_SECONDS * 1000 - 200)
      expect(bandEnd).toBeGreaterThan(answeringSince)
      expect(await shownSinceFirstNotice(projector)).toEqual(['band', 'answering'])
      expect(await shownSinceFirstNotice(presenter)).toEqual(['answering'])
      await callFake(presenter, 'endPlayback')
      await showsPhase(projector, 'listening', PILL.de.listening)
      await showsPhase(presenter, 'listening', PILL.de.listening)
    })
  })

  it.each([
    ['a pending microphone permission', { microphone: 'pending' }, false],
    ['a denied microphone', { microphone: 'denied' }, true],
    ['a worklet that fails to load', { worklet: 'failing' }, true],
  ] as [string, Parameters<typeof setFakes>[1], boolean][])('CP8: shows nothing for 4 s with %s', async (_situation, fakes, failsLoudly) => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await setFakes(presenter, fakes)
      await switchOn(presenter)
      await expect.poll(() => presenter.evaluate(
        () => (window as unknown as { __microphonesRequested: number }).__microphonesRequested,
      ), { timeout: 5_000 }).toBeGreaterThanOrEqual(1)
      await expectNothingFor([presenter, projector], 4_000)
      if (failsLoudly) await presenter.getByText('No hearing').waitFor({ state: 'visible' })
    })
  })

  it('CP9: starts the band when the pending microphone is allowed and lasts ten seconds from then', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await setFakes(presenter, { microphone: 'pending' })
      await switchOn(presenter)
      await presenter.waitForTimeout(1_000)
      expect(await readNotice(projector)).toBeNull()
      await callFake(presenter, 'releaseMicrophone')
      const allowedAt = Date.now()
      await showsBand(projector)
      await showsPhase(presenter, 'listening', PILL.de.listening)
      await showsPhase(projector, 'listening', PILL.de.listening)
      const sinceAllowed = (await phaseStartedAt(projector, 'listening')) - allowedAt
      console.info(`band lasted ${sinceAllowed} ms from the allowed microphone`)
      expect(sinceAllowed).toBeGreaterThanOrEqual(BAND_SECONDS * 1000 - 200)
      expect(sinceAllowed).toBeLessThanOrEqual(BAND_SECONDS * 1000 + 2_000)
    })
  })

  it.each([
    ['local hearing that is not ready', 'not-ready', 'Local hearing not ready'],
    ['speech that cannot be reached', 'unreachable', 'Voice offline'],
    ['a /who that fails', 'who-fails', 'Unreachable'],
    ['a /who that finds no deck', 'who-not-found', 'Unreachable'],
    ['a hearing the server refuses', 'refused-on-open', 'No hearing'],
  ] as const)('CP8: shows nothing for 4 s with %s', async (_situation, mode, failure) => {
    state.hearingMode = mode
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await presenter.getByText(failure, { exact: true }).waitFor({ state: 'visible' })
      await expectNothingFor([presenter, projector], 4_000)
    })
  })

  it.each([
    ['cannot start', 'pending'],
    ['is refused by the player', 'rejected'],
  ] as const)('CP9: shows the band and then the listening pill, never the answering pill, while the first answer %s', async (_situation, playback) => {
    await inRoom(async ({ presenter, projector }) => {
      await setFakes(presenter, { playback })
      await switchOn(presenter)
      if (playback === 'pending') await expect.poll(async () => (await snapshot(presenter)).speaking, { timeout: 5_000 }).toBe(true)
      else await presenter.getByText('No speech', { exact: true }).waitFor({ state: 'visible' })
      await showsBand(projector)
      await showsPhase(projector, 'listening', PILL.de.listening)
      await presenter.waitForTimeout(1_500)
      expect(await shownSinceFirstNotice(projector)).toEqual(['band', 'listening'])
      expect(await shownSinceFirstNotice(presenter)).toEqual(['listening'])
    })
  })

  it('Never shows the answering pill for a later answer whose play is refused', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'listening', PILL.de.listening)
      await setFakes(presenter, { playback: 'rejected' })
      const marks = [await markTimeline(presenter), await markTimeline(projector)]
      sendHeard('Another question about the handoff')
      await presenter.getByText('No speech', { exact: true }).waitFor({ state: 'visible' })
      await presenter.waitForTimeout(1_500)
      expect(new Set(await phasesSince(projector, marks[1]!))).toEqual(new Set())
      expect(new Set(await phasesSince(presenter, marks[0]!))).toEqual(new Set())
      await showsPhase(projector, 'listening', PILL.de.listening)
    })
  })

  it('CP9: keeps the answering pill while the playing sound goes on, when a queued sound fails', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      state.answerMode = 'two-sounds'
      await setFakes(presenter, { playback: 'second-fails-on-cue' })
      await switchOn(presenter)
      await showsPhase(projector, 'listening', PILL.de.listening)
      sendHeard('Answer both sounds')
      await showsPhase(projector, 'answering', PILL.de.answering)
      await expect.poll(() => presenter.evaluate(() => (window as unknown as Probed).__fakes.failSecond !== null)).toBe(true)
      await presenter.evaluate(() => (window as unknown as Probed).__fakes.failSecond?.())
      await presenter.getByText('No speech', { exact: true }).waitFor({ state: 'visible' })
      await presenter.waitForTimeout(1_500)
      await showsPhase(projector, 'answering', PILL.de.answering)
      await callFake(presenter, 'endPlayback')
      await showsPhase(projector, 'listening', PILL.de.listening)
    })
  })

  it.each([
    ['not-enabled', 'Not enabled'],
    ['signed-out-refresh', 'Sign in again'],
    ['no-session', 'Sign in again'],
  ] as const)('CP8: keeps the notice when the ask is refused (%s)', async (mode, line) => {
    await inRoom(async ({ presenter, projector }) => {
      state.answerMode = mode
      await switchOn(presenter)
      await presenter.getByText(line, { exact: true }).waitFor({ state: 'visible' })
      await presenter.waitForTimeout(1_500)
      await showsBand(projector)
      await showsPhase(presenter, 'listening', PILL.de.listening)
      await showsPhase(projector, 'listening', PILL.de.listening)
      expect(await shownSinceFirstNotice(projector)).toEqual(['band', 'listening'])

      state.answerMode = 'audio'
      sendHeard('Answer this one')
      await showsPhase(projector, 'answering', PILL.de.answering)
      await callFake(presenter, 'endPlayback')
      await showsPhase(projector, 'listening', PILL.de.listening)
      state.answerMode = mode
      const marks = [await markTimeline(presenter), await markTimeline(projector)]
      sendHeard('And this one')
      await presenter.getByText(line, { exact: true }).waitFor({ state: 'visible' })
      await presenter.waitForTimeout(1_500)
      await showsPhase(projector, 'listening', PILL.de.listening)
      await showsPhase(presenter, 'listening', PILL.de.listening)
      expect(await phasesSince(projector, marks[1]!)).toEqual([])
      expect(await phasesSince(presenter, marks[0]!)).toEqual([])
    })
  })

  it('CP8: clears both windows, with no fault word, when the server ends the hearing in order', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsBand(projector)
      endHearings(HopClose.NORMAL)
      await clearanceOf([presenter, projector], 'the server ends the hearing in order')
      expect(await presenter.getByText('No hearing', { exact: true }).count()).toBe(0)
    })
  })

  it('CP8: clears both windows when the hearing fails after the notice', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsBand(projector)
      endHearings(HopClose.SERVICE_FAILED)
      await clearanceOf([presenter, projector], 'the hearing fails after the notice')
      await presenter.getByText('No hearing', { exact: true }).waitFor({ state: 'visible' })
    })
  })

  it('CP9: clears when the AI is switched off and shows the band again when it is switched on again', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'listening', PILL.de.listening)
      await presenter.getByRole('switch', { name: 'AI' }).click()
      await clearanceOf([presenter, projector], 'the AI is switched off')
      const marks = [await markTimeline(presenter), await markTimeline(projector)]
      await switchOn(presenter)
      await showsBand(projector)
      await showsPhase(presenter, 'listening', PILL.de.listening)
      await presenter.waitForTimeout(1_500)
      expect(new Set(await phasesSince(projector, marks[1]!))).toEqual(new Set(['band']))
      expect(new Set(await phasesSince(presenter, marks[0]!))).toEqual(new Set(['listening']))
      await showsPhase(projector, 'listening', PILL.de.listening)
    })
  })

  it('CP8: shows nothing when the AI is never switched on', async () => {
    await inRoom(async ({ presenter, projector }) => {
      await expectNothingFor([presenter, projector], 2_000)
      expect((await snapshot(presenter)).on).toBe(false)
      await saveEvidence(projector, 'room-off.png')
    })
  })

  it('CP9: shows the band on a projector opened later for its first ten seconds, then the pill', async () => {
    state.heardOnConnect = false
    const { browser, context } = await openRoomBrowser()
    try {
      const presenter = await openPageIn(context, `${state.origin}/presenter/1`, 1024)
      await switchOn(presenter)
      await showsPhase(presenter, 'listening', PILL.de.listening)
      await presenter.waitForTimeout((BAND_SECONDS + 1) * 1000)
      let opened = Date.now()
      const lateProjector = await recordedProjector(context)
      await showsBand(lateProjector, 'de', 2_500)
      console.info(`band on a late projector after ${Date.now() - opened} ms`)
      await showsPhase(lateProjector, 'listening', PILL.de.listening)
      expect(await shownSinceFirstNotice(lateProjector)).toEqual(['band', 'listening'])

      sendHeard('First question')
      await showsPhase(presenter, 'answering', PILL.de.answering)
      opened = Date.now()
      const afterAnswerProjector = await recordedProjector(context)
      await showsBand(afterAnswerProjector, 'de', 2_500)
      console.info(`band on a projector opened during an answer after ${Date.now() - opened} ms`)
      await showsPhase(afterAnswerProjector, 'answering', PILL.de.answering)
    } finally {
      await browser.close()
    }
  })

  it('Clears the projector by the OFF post when the presenter page closes', async () => {
    state.heardOnConnect = false
    const { browser, context } = await openRoomBrowser()
    try {
      const presenter = await openPageIn(context, `${state.origin}/presenter/1`, 1024)
      const projector = await openPageIn(context, `${state.origin}/1`, 1024, async (page) => {
        await page.addInitScript(() => {
          const log: { phase: string, at: number }[] = []
          ;(window as unknown as Probed).__roomMessages = log
          const RealChannel = window.BroadcastChannel
          window.BroadcastChannel = class extends RealChannel {
            constructor(name: string) {
              super(name)
              this.addEventListener('message', (event: MessageEvent<{ phase?: string }>) => {
                if (event.data?.phase) log.push({ phase: event.data.phase, at: Date.now() })
              })
            }
          }
        })
      })
      await switchOn(presenter)
      await showsBand(projector)
      const closed = Date.now()
      await presenter.close()
      await isEmpty(projector, 2_500)
      console.info(`projector empty ${Date.now() - closed} ms after the presenter page closed`)
      const messages = await projector.evaluate(() => (window as unknown as Probed).__roomMessages)
      expect(messages.map((message) => message.phase)).toContain('off')
    } finally {
      await browser.close()
    }
  })

  it('CP9: expires on a frozen presenter by itself, writes nothing back and shows the band again after the resume', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsBand(projector)
      await showsPhase(presenter, 'listening', PILL.de.listening)
      const resume = await freezePage(presenter)
      const frozenAt = Date.now()
      await isEmpty(projector, 10_000)
      const expiredAfter = Date.now() - frozenAt
      console.info(`projector empty ${expiredAfter} ms after the presenter froze`)
      expect(expiredAfter).toBeLessThan(4_000)
      await resume()
      await presenter.evaluate(() => true)
      await showsBand(projector, 'de', 2_500)
      await showsPhase(projector, 'listening', PILL.de.listening)
      await presenter.waitForTimeout(2_000)
      const entries = await timelineOf(presenter)
      const firstShown = entries.findIndex((entry) => entry.phase !== null)
      expect(entries.slice(firstShown).map((entry) => entry.phase)).not.toContain(null)
    })
  })

  it('CP8: clears the projector by the teardown OFF when the presenter switches to Play Mode', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsBand(projector)
      await presenter.getByTitle('Play Mode', { exact: true }).click()
      await presenter.waitForURL(`${state.origin}/1`)
      await isEmpty(projector, 1_000)
      await projector.waitForTimeout(4_000)
      expect(await readNotice(projector)).toBeNull()
    })
  })

  it.each([
    ['/1?print', 'Hallo Co-Presenter'],
    ['/overview', 'Hallo Co-Presenter'],
    ['/notes', 'Begrüsse das Publikum'],
  ])('CP8: shows no notice on %s', async (path, marker) => {
    state.heardOnConnect = false
    await inRoom(async ({ context, presenter, projector }) => {
      await switchOn(presenter)
      await showsBand(projector)
      const printed = await openPageIn(context, `${state.origin}${path}`, 1024)
      await printed.getByText(marker).first().waitFor({ state: 'attached' })
      await printed.waitForTimeout(2_500)
      expect(await printed.locator('[data-room-notice]').count()).toBe(0)
    })
  })

  it('CP9: the band and the pill are no control, leave the slide where it is and stay within a fifth of it', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      const container = projector.locator('.slidev-slide-container').first()
      const boxBefore = await container.boundingBox()
      await switchOn(presenter)
      for (const [phase, text] of [['band', BANNER.de], ['listening', PILL.de.listening]] as const) {
        await showsPhase(projector, phase, text)
        const notice = projector.locator('[data-room-notice]')
        expect(await notice.locator('button, a, input, [role]').count()).toBe(0)
        expect(await projector.getByRole('switch', { name: 'AI' }).count()).toBe(0)
        expect(await projector.getByRole('link', { name: 'Home' }).count()).toBe(0)
        const noticeBox = (await notice.boundingBox())!
        const covered = await projector.evaluate(([x, y]) => (
          document.elementFromPoint(x!, y!)?.closest('[data-room-notice]') ?? null
        ) !== null, [noticeBox.x + noticeBox.width / 2, noticeBox.y + noticeBox.height / 2])
        expect(covered).toBe(false)
        const selectedText = await projector.evaluate(() => {
          getSelection()!.selectAllChildren(document.body)
          return getSelection()!.toString()
        })
        expect(selectedText).not.toContain(text)
        expect(await container.boundingBox()).toEqual(boxBefore)
        if (phase === 'band') expect(noticeBox.height).toBeLessThanOrEqual(boxBefore!.height / 5)
      }
    })
  })

  it.each(['de', 'en'] as const)('CP9: sets the band in at most two lines in %s', async (language) => {
    state.whoLanguage = language
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsBand(projector, language)
      const lines = await projector.evaluate(() => {
        const range = document.createRange()
        range.selectNodeContents(document.querySelector('[data-room-notice="band"]')!)
        return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size
      })
      expect(lines).toBeLessThanOrEqual(2)
      await saveEvidence(projector, `room-band-lines-${language}.png`)
    })
  })

  it('inverts the colours of the band and the pill on a dark slide', async () => {
    state.heardOnConnect = false
    const luminanceOf = (page: Page) => page.evaluate(() => {
      const style = getComputedStyle(document.querySelector('[data-room-notice]')!)
      const lightness = (color: string) => {
        const [red = 0, green = 0, blue = 0] = color.match(/[\d.]+/g)!.map(Number)
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue
      }
      return { background: lightness(style.backgroundColor), text: lightness(style.color) }
    })
    const contrasts = {} as Record<string, { band: boolean, pill: boolean }>
    for (const scheme of ['light', 'dark'] as const) {
      await inRoom(async ({ presenter, projector }) => {
        await switchOn(presenter)
        await showsBand(projector)
        const band = await luminanceOf(projector)
        await showsPhase(presenter, 'listening', PILL.de.listening)
        const pill = await luminanceOf(presenter)
        contrasts[scheme] = { band: band.background > band.text, pill: pill.background > pill.text }
        await saveEvidence(projector, `room-band-${scheme}-1024.png`)
      }, { colorScheme: scheme })
    }
    expect(contrasts).toEqual({ light: { band: false, pill: false }, dark: { band: true, pill: true } })
  })

  it.each([1024, 390])('CP9: shows the presenter preview the pill only and whole inside the preview at %ipx', async (width) => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter }) => {
      await switchOn(presenter)
      const expectWholeInPreview = async (phase: string) => {
        await expect.poll(async () => (await readNotice(presenter))?.phase, { timeout: 5_000 }).toBe(phase)
        const previewBox = (await presenter.locator('.slidev-slide-container').first().boundingBox())!
        const noticeBox = (await presenter.locator('[data-room-notice]').boundingBox())!
        console.info(`${phase}: preview ${JSON.stringify(previewBox)}, notice ${JSON.stringify(noticeBox)}, viewport width ${width}`)
        expect(noticeBox.x).toBeGreaterThanOrEqual(previewBox.x - 1)
        expect(noticeBox.y).toBeGreaterThanOrEqual(previewBox.y - 1)
        expect(noticeBox.x + noticeBox.width).toBeLessThanOrEqual(previewBox.x + previewBox.width + 1)
        expect(noticeBox.y + noticeBox.height).toBeLessThanOrEqual(previewBox.y + previewBox.height + 1)
        await saveEvidence(presenter, `room-presenter-${phase}-${width}.png`)
      }
      await expectWholeInPreview('listening')
      sendHeard('Question')
      await expectWholeInPreview('answering')
      await callFake(presenter, 'endPlayback')
      await expectWholeInPreview('listening')
      expect(await phasesSince(presenter, 0)).not.toContain('band')
    }, { presenterWidth: width })
  })

  it('A second presenter page that was never on does not clear the room', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ context, presenter, projector }) => {
      await switchOn(presenter)
      await showsBand(projector)
      const second = await openPageIn(context, `${state.origin}/presenter/1`, 1024)
      await second.waitForTimeout(1_000)
      await second.getByTitle('Play Mode', { exact: true }).click()
      await second.waitForURL(`${state.origin}/1`)
      await second.close()
      await projector.waitForTimeout(2_000)
      const entries = await timelineOf(projector)
      const firstShown = entries.findIndex((entry) => entry.phase !== null)
      expect(entries.slice(firstShown).map((entry) => entry.phase)).not.toContain(null)
      expect(['band', 'listening']).toContain((await readNotice(projector))?.phase)
    })
  })

  it('Two addon decks on one origin keep their notices apart', async () => {
    const directories = [await buildAddonDeck('/deck/a/'), await buildAddonDeck('/deck/50%25off/')]
    const { server, origin, sockets } = await createOverlayDeckServer(directories[0]!, {
      basePath: '/deck/a',
      alsoServing: [{ directory: directories[1]!, basePath: '/deck/50%25off' }],
    })
    const { browser, context } = await openRoomBrowser()
    try {
      const presenterA = await openPageIn(context, `${origin}/deck/a/presenter/1`, 1024)
      const projectorA = await openPageIn(context, `${origin}/deck/a/1`, 1024)
      const projectorB = await openPageIn(context, `${origin}/deck/50%25off/1`, 1024)
      await recordNotice(projectorB)
      await switchOn(presenterA)
      await showsBand(projectorA)
      await showsPhase(presenterA, 'listening', PILL.de.listening)
      await projectorB.waitForTimeout(4_000)
      expect(await phasesSince(projectorB, 0)).toEqual([])
    } finally {
      await browser.close()
      for (const socket of sockets) socket.destroy()
      server.closeAllConnections()
      await new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())))
      await Promise.all(directories.map((directory) => rm(directory, { recursive: true, force: true })))
    }
  }, 180_000)
})

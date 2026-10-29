import { rm } from 'node:fs/promises'

import type { BrowserContext, Page } from 'playwright-chromium'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  buildAddonDeck,
  closeHearingLeases,
  createOverlayDeckServer,
  freezePage,
  openPageIn,
  openRoomBrowser,
  registerOverlayHarness,
  saveEvidence,
  sendHeard,
  sendHearingError,
  state,
} from './presenter-overlay-harness'

registerOverlayHarness()
vi.setConfig({ testTimeout: 60_000 })

// The ruled sentences (#293 E8, line 5 A) and pill words, as scenario data.
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
  playback: 'resolve' | 'pending' | 'rejected'
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

async function switchOn(presenter: Page) {
  const ai = presenter.getByRole('switch', { name: 'AI' })
  await ai.waitFor({ state: 'visible', timeout: 2_000 })
  await ai.click({ timeout: 2_000 })
}
const waitForCapture = (presenter: Page) =>
  expect.poll(async () => (await snapshot(presenter)).capturing, { timeout: 5_000 }).toBe(true)
const showsPhase = (page: Page, phase: string, text: string, timeout = 5_000) =>
  expect.poll(() => readNotice(page), { timeout, interval: 50 }).toEqual({ phase, text })
const isEmpty = (page: Page, timeout: number) =>
  expect.poll(() => readNotice(page), { timeout, interval: 50 }).toBeNull()

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
    'Cells 1, 8, 20: the banner, then the answering pill, then the listening pill in %s',
    async (language) => {
      state.whoLanguage = language
      state.heardOnConnect = false
      await inRoom(async ({ presenter, projector }) => {
        await switchOn(presenter)
        await waitForCapture(presenter)
        await showsPhase(projector, 'banner', BANNER[language])
        await showsPhase(presenter, 'banner', BANNER[language])
        await saveEvidence(projector, `room-banner-${language}-1024.png`)
        await saveEvidence(presenter, `room-presenter-banner-${language}.png`)
        sendHeard('Where does the handoff slow down?')
        await showsPhase(projector, 'answering', PILL[language].answering)
        await showsPhase(presenter, 'answering', PILL[language].answering)
        await saveEvidence(projector, `room-answering-${language}-1024.png`)
        await callFake(presenter, 'endPlayback')
        await showsPhase(projector, 'listening', PILL[language].listening)
        await showsPhase(presenter, 'listening', PILL[language].listening)
        await saveEvidence(projector, `room-listening-${language}-1024.png`)
        expect(await presenter.getByRole('switch', { name: 'AI' }).textContent()).toContain('AI')
      })
    },
  )

  it.each([
    ['a pending microphone permission', { microphone: 'pending' }, false],
    ['a denied microphone', { microphone: 'denied' }, true],
    ['a worklet that fails to load', { worklet: 'failing' }, true],
  ] as [string, Parameters<typeof setFakes>[1], boolean][])('Cells 2-4: shows nothing for 4 s with %s', async (_situation, fakes, failsLoudly) => {
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

  it('Cell 2: shows the banner once the pending microphone is allowed', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await setFakes(presenter, { microphone: 'pending' })
      await switchOn(presenter)
      await presenter.waitForTimeout(1_000)
      expect(await readNotice(projector)).toBeNull()
      await callFake(presenter, 'releaseMicrophone')
      await showsPhase(projector, 'banner', BANNER.de)
      await showsPhase(presenter, 'banner', BANNER.de)
    })
  })

  it.each([
    ['local hearing that is not ready', 'not-ready', 'Local hearing not ready'],
    ['speech that cannot be reached', 'unreachable', 'Voice offline'],
    ['a /who that fails', 'who-fails', 'Unreachable'],
    ['a /who that finds no deck', 'who-not-found', 'Unreachable'],
    ['a hearing socket that errors before capture', 'error-on-open', 'No hearing'],
  ] as const)('Cell 5: shows nothing for 4 s with %s', async (_situation, mode, failure) => {
    state.hearingMode = mode
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await presenter.getByText(failure, { exact: true }).waitFor({ state: 'visible' })
      await expectNothingFor([presenter, projector], 4_000)
    })
  })

  it('Cell 6: keeps the banner and shows no pill while the first answer cannot start', async () => {
    await inRoom(async ({ presenter, projector }) => {
      await setFakes(presenter, { playback: 'pending' })
      await switchOn(presenter)
      await expect.poll(async () => (await snapshot(presenter)).speaking, { timeout: 5_000 }).toBe(true)
      await presenter.waitForTimeout(2_000)
      for (const page of [presenter, projector]) {
        expect(new Set(await phasesSince(page, 0))).toEqual(new Set(['banner']))
      }
      expect(await readNotice(projector)).toEqual({ phase: 'banner', text: BANNER.de })
    })
  })

  it('Cell 7: keeps the banner and shows no pill when the first answer is refused by the player', async () => {
    await inRoom(async ({ presenter, projector }) => {
      await setFakes(presenter, { playback: 'rejected' })
      await switchOn(presenter)
      await presenter.getByText('No speech', { exact: true }).waitFor({ state: 'visible' })
      await presenter.waitForTimeout(2_000)
      for (const page of [presenter, projector]) {
        expect(new Set(await phasesSince(page, 0))).toEqual(new Set(['banner']))
      }
      expect(await readNotice(projector)).toEqual({ phase: 'banner', text: BANNER.de })
    })
  })

  it('Cell 8: shows the answering pill while audio plays and the listening pill after it ends', async () => {
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'answering', PILL.de.answering)
      await showsPhase(presenter, 'answering', PILL.de.answering)
      await callFake(presenter, 'endPlayback')
      await showsPhase(projector, 'listening', PILL.de.listening)
    })
  })

  it('Cell 9: never shows the answering pill for a later answer whose play is refused', async () => {
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'answering', PILL.de.answering)
      await callFake(presenter, 'endPlayback')
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

  it.each([
    ['not-enabled', 'Not enabled'],
    ['signed-out-refresh', 'Sign in again'],
    ['no-session', 'Sign in again'],
  ] as const)('Cell 10: keeps the notice when the ask is refused (%s)', async (mode, line) => {
    await inRoom(async ({ presenter, projector }) => {
      state.answerMode = mode
      await switchOn(presenter)
      await presenter.getByText(line, { exact: true }).waitFor({ state: 'visible' })
      await presenter.waitForTimeout(1_500)
      await showsPhase(projector, 'banner', BANNER.de)
      await showsPhase(presenter, 'banner', BANNER.de)
      expect(new Set(await phasesSince(projector, 0))).toEqual(new Set(['banner']))

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

  it('Cell 11: clears both windows when the server ends the hearing lease', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'banner', BANNER.de)
      closeHearingLeases()
      await clearanceOf([presenter, projector], 'Cell 11')
      await presenter.getByText('No hearing', { exact: true }).waitFor({ state: 'visible' })
    })
  })

  it('Cell 12: clears both windows when capture fails after the notice', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'banner', BANNER.de)
      sendHearingError()
      await clearanceOf([presenter, projector], 'Cell 12')
    })
  })

  it('Cell 13: clears when the AI is switched off and starts with the banner when it is switched on again', async () => {
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'answering', PILL.de.answering)
      await callFake(presenter, 'endPlayback')
      await showsPhase(projector, 'listening', PILL.de.listening)
      await presenter.getByRole('switch', { name: 'AI' }).click()
      await clearanceOf([presenter, projector], 'Cell 13 (switch-off)')
      state.heardOnConnect = false
      const marks = [await markTimeline(presenter), await markTimeline(projector)]
      await switchOn(presenter)
      await showsPhase(projector, 'banner', BANNER.de)
      await showsPhase(presenter, 'banner', BANNER.de)
      await presenter.waitForTimeout(1_500)
      expect(new Set(await phasesSince(projector, marks[1]!))).toEqual(new Set(['banner']))
      expect(new Set(await phasesSince(presenter, marks[0]!))).toEqual(new Set(['banner']))
    })
  })

  it('Cell 14: shows nothing when the AI is never switched on', async () => {
    await inRoom(async ({ presenter, projector }) => {
      await expectNothingFor([presenter, projector], 2_000)
      expect((await snapshot(presenter)).on).toBe(false)
      await saveEvidence(projector, 'room-off.png')
    })
  })

  it('Cell 15: a projector opened later shows the banner and then the pill within 2.5 s', async () => {
    state.heardOnConnect = false
    const { browser, context } = await openRoomBrowser()
    try {
      const presenter = await openPageIn(context, `${state.origin}/presenter/1`, 1024)
      await switchOn(presenter)
      await showsPhase(presenter, 'banner', BANNER.de)
      let opened = Date.now()
      const lateProjector = await openPageIn(context, `${state.origin}/1`, 1024)
      await showsPhase(lateProjector, 'banner', BANNER.de, 2_500)
      console.info(`Cell 15: banner on a late projector after ${Date.now() - opened} ms`)

      sendHeard('First question')
      await showsPhase(presenter, 'answering', PILL.de.answering)
      await callFake(presenter, 'endPlayback')
      await showsPhase(presenter, 'listening', PILL.de.listening)
      opened = Date.now()
      const afterAnswerProjector = await openPageIn(context, `${state.origin}/1`, 1024)
      await showsPhase(afterAnswerProjector, 'listening', PILL.de.listening, 2_500)
      console.info(`Cell 15: pill on a late projector after ${Date.now() - opened} ms`)
    } finally {
      await browser.close()
    }
  })

  it('Cell 16: clears the projector by the OFF post when the presenter page closes', async () => {
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
      await showsPhase(projector, 'banner', BANNER.de)
      const closed = Date.now()
      await presenter.close()
      await isEmpty(projector, 2_500)
      console.info(`Cell 16: projector empty ${Date.now() - closed} ms after the presenter page closed`)
      const messages = await projector.evaluate(() => (window as unknown as Probed).__roomMessages)
      expect(messages.map((message) => message.phase)).toContain('off')
    } finally {
      await browser.close()
    }
  })

  it('Cell 17: expires on a frozen presenter by itself and writes nothing back', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'banner', BANNER.de)
      await showsPhase(presenter, 'banner', BANNER.de)
      const resume = await freezePage(presenter)
      const frozenAt = Date.now()
      await isEmpty(projector, 10_000)
      const expiredAfter = Date.now() - frozenAt
      console.info(`Cell 17: projector empty ${expiredAfter} ms after the presenter froze`)
      expect(expiredAfter).toBeLessThan(4_000)
      await resume()
      await presenter.evaluate(() => true)
      await showsPhase(projector, 'banner', BANNER.de, 2_500)
      await presenter.waitForTimeout(2_000)
      const entries = await timelineOf(presenter)
      const firstShown = entries.findIndex((entry) => entry.phase !== null)
      expect(entries.slice(firstShown).map((entry) => entry.phase)).not.toContain(null)
    })
  })

  it.each([
    ['/1?print', 'Hallo Co-Presenter'],
    ['/overview', 'Hallo Co-Presenter'],
    ['/notes', 'Begrüsse das Publikum'],
  ])('Cell 18: shows no notice on %s', async (path, marker) => {
    state.heardOnConnect = false
    await inRoom(async ({ context, presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'banner', BANNER.de)
      const printed = await openPageIn(context, `${state.origin}${path}`, 1024)
      await printed.getByText(marker).first().waitFor({ state: 'attached' })
      await printed.waitForTimeout(2_500)
      expect(await printed.locator('[data-room-notice]').count()).toBe(0)
    })
  })

  it('Cell 19: is no control and leaves the slide where it is', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter, projector }) => {
      const container = projector.locator('.slidev-slide-container').first()
      const boxBefore = await container.boundingBox()
      await switchOn(presenter)
      for (const [phase, text] of [['banner', BANNER.de], ['listening', PILL.de.listening]] as const) {
        if (phase === 'listening') {
          sendHeard('Question')
          await showsPhase(projector, 'answering', PILL.de.answering)
          await callFake(presenter, 'endPlayback')
        }
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
        expect(await container.boundingBox()).toEqual(boxBefore)
      }
    })
  })

  it('Sentence 5: inverts the colours on a dark slide', async () => {
    state.heardOnConnect = false
    const luminanceOf = (page: Page) => page.evaluate(() => {
      const style = getComputedStyle(document.querySelector('[data-room-notice]')!)
      const lightness = (color: string) => {
        const [red = 0, green = 0, blue = 0] = color.match(/[\d.]+/g)!.map(Number)
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue
      }
      return { background: lightness(style.backgroundColor), text: lightness(style.color) }
    })
    const contrasts = {} as Record<string, boolean>
    for (const scheme of ['light', 'dark'] as const) {
      await inRoom(async ({ presenter, projector }) => {
        await switchOn(presenter)
        await showsPhase(projector, 'banner', BANNER.de)
        const { background, text } = await luminanceOf(projector)
        contrasts[scheme] = background > text
        await saveEvidence(projector, `room-banner-${scheme}-1024.png`)
      }, { colorScheme: scheme })
      state.heardOnConnect = false
    }
    expect(contrasts).toEqual({ light: false, dark: true })
  })

  it('Cell 22: scales the banner and the pill whole inside the presenter preview at 390 px', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ presenter }) => {
      await switchOn(presenter)
      for (const phase of ['banner', 'listening'] as const) {
        if (phase === 'listening') {
          sendHeard('Question')
          await expect.poll(async () => (await readNotice(presenter))?.phase, { timeout: 5_000 }).toBe('answering')
          await callFake(presenter, 'endPlayback')
        }
        await expect.poll(async () => (await readNotice(presenter))?.phase, { timeout: 5_000 }).toBe(phase)
        const previewBox = (await presenter.locator('.slidev-slide-container').first().boundingBox())!
        const noticeBox = (await presenter.locator('[data-room-notice]').boundingBox())!
        console.info(`Cell 22 ${phase}: preview ${JSON.stringify(previewBox)}, notice ${JSON.stringify(noticeBox)}, viewport width 390`)
        expect(noticeBox.x).toBeGreaterThanOrEqual(previewBox.x - 1)
        expect(noticeBox.y).toBeGreaterThanOrEqual(previewBox.y - 1)
        expect(noticeBox.x + noticeBox.width).toBeLessThanOrEqual(previewBox.x + previewBox.width + 1)
        expect(noticeBox.y + noticeBox.height).toBeLessThanOrEqual(previewBox.y + previewBox.height + 1)
        await saveEvidence(presenter, `room-presenter-${phase}-narrow.png`)
      }
    }, { presenterWidth: 390 })
  })

  it('Cell 24: a second presenter page that was never on does not clear the room', async () => {
    state.heardOnConnect = false
    await inRoom(async ({ context, presenter, projector }) => {
      await switchOn(presenter)
      await showsPhase(projector, 'banner', BANNER.de)
      const second = await openPageIn(context, `${state.origin}/presenter/1`, 1024)
      await second.waitForTimeout(1_000)
      await second.close()
      await projector.waitForTimeout(2_000)
      const entries = await timelineOf(projector)
      const firstShown = entries.findIndex((entry) => entry.phase !== null)
      expect(entries.slice(firstShown).map((entry) => entry.phase)).not.toContain(null)
      expect(await readNotice(projector)).toEqual({ phase: 'banner', text: BANNER.de })
    })
  })

  it('Cells 21, 23: two addon decks on one origin keep their notices apart', async () => {
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
      await showsPhase(projectorA, 'banner', BANNER.de)
      await showsPhase(presenterA, 'banner', BANNER.de)
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

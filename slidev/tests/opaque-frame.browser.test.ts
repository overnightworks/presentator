import { cp, mkdtemp, rm } from 'node:fs/promises'
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { Browser, BrowserContext, Frame, Page } from 'playwright-chromium'

import { buildStagedDeck, closeBrowser, launchBrowser, serveFile } from './presenter-overlay-harness'

vi.setConfig({ testTimeout: 180_000, hookTimeout: 180_000 })

// Spike for a deck framed on an opaque origin: the harness below is the frame
// host, the token path and the page-side relay; the deck's own bridge lives in
// the fixture's `global-top.vue`.
const deckFixture = fileURLToPath(new URL('./fixtures/opaque-frame-deck', import.meta.url))
const TOKEN = 'k3j9x0q2'
const SANDBOX = 'allow-scripts'

type ContentRequest = { path: string, fetchMode: string | undefined, hasCookie: boolean }
type Loopback = { server: Server, origin: string }

const contentRequests: ContentRequest[] = []
const contentResponseHeaders: Promise<Record<string, string>>[] = []
let buildDirectory = ''
let deckDirectory = ''
let content: Loopback
let stage: Loopback
let browser: Browser
let context: BrowserContext

function listen(handler: (request: IncomingMessage, response: ServerResponse) => void): Promise<Loopback> {
  const server = createServer(handler)
  return new Promise((done) => {
    server.listen(0, '127.0.0.1', () => {
      done({ server, origin: `http://127.0.0.1:${(server.address() as AddressInfo).port}` })
    })
  })
}

function serveBuild(request: IncomingMessage, response: ServerResponse) {
  const { pathname } = new URL(request.url ?? '/', 'http://content.test')
  const prefix = `/t/${TOKEN}/`
  contentRequests.push({
    path: pathname,
    fetchMode: request.headers['sec-fetch-mode'] as string | undefined,
    hasCookie: 'cookie' in request.headers,
  })
  if (!pathname.startsWith(prefix)) {
    response.writeHead(404).end()
    return
  }
  return serveFile(buildDirectory, pathname.slice(prefix.length - 1), response, { 'access-control-allow-origin': '*' })
}

// The stage page frames the build and relays between windows through a
// BroadcastChannel on the stage origin; a message from the frame is untrusted.
function stagePage(role: string) {
  return `<!doctype html><title>${role}</title>
<iframe id="deck" sandbox="${SANDBOX}" src="${content.origin}/t/${TOKEN}/" style="width:960px;height:540px;border:0"></iframe>
<script>
const frame = document.getElementById('deck')
const channel = new BroadcastChannel('stage')
let total = 0
window.seen = []
const isIntegerWithin = (value, minimum, maximum) => Number.isInteger(value) && value >= minimum && value <= maximum
const toFrame = (slide, click) => frame.contentWindow.postMessage({ type: 'go', slide, click }, '*')
window.goTo = (slide, click) => { toFrame(slide, click) }
let clicksTotal = 0
addEventListener('message', (event) => {
  if (event.source !== frame.contentWindow) return
  const message = event.data
  if (message?.type !== 'state') return
  if (!isIntegerWithin(message.total, 1, 1000)) return
  if (!isIntegerWithin(message.clicksTotal, 0, 1000)) return
  if (!isIntegerWithin(message.slide, 1, message.total) || !isIntegerWithin(message.click, 0, message.clicksTotal)) return
  total = message.total
  clicksTotal = message.clicksTotal
  window.seen.push([message.slide, message.click])
  if (${role === 'presenter'}) channel.postMessage({ slide: message.slide, click: message.click })
})
channel.onmessage = (event) => {
  const { slide, click } = event.data
  if (isIntegerWithin(slide, 1, total) && isIntegerWithin(click, 0, clicksTotal)) toFrame(slide, click)
}
</script>`
}

beforeAll(async () => {
  deckDirectory = await mkdtemp(join(tmpdir(), 'opaque-frame-deck-'))
  buildDirectory = await mkdtemp(join(tmpdir(), 'opaque-frame-build-'))
  await cp(deckFixture, deckDirectory, { recursive: true })
  await buildStagedDeck(deckDirectory, buildDirectory, `/t/${TOKEN}/`)
  content = await listen((request, response) => { void serveBuild(request, response) })
  stage = await listen((request, response) => {
    const role = request.url?.slice(1) ?? ''
    response.writeHead(200, { 'content-type': 'text/html' }).end(stagePage(role))
  })
  browser = await launchBrowser()
  context = await browser.newContext()
  context.on('response', (response) => {
    if (response.url().startsWith(content.origin)) contentResponseHeaders.push(response.allHeaders())
  })
})

afterAll(async () => {
  await closeBrowser(browser)
  content.server.close()
  stage.server.close()
  await rm(deckDirectory, { recursive: true, force: true })
  await rm(buildDirectory, { recursive: true, force: true })
})

type Opened = { page: Page, deck: Frame, problems: string[] }

// Every page a test opens is closed after it, so a failed test leaves no page
// on the shared stage origin's BroadcastChannel to relay into the next one.
const openedPages: Page[] = []

afterEach(async () => {
  await Promise.all(openedPages.splice(0).map((page) => page.close()))
})

async function openStage(role: 'presenter' | 'projector'): Promise<Opened> {
  const page = await context.newPage()
  openedPages.push(page)
  const problems: string[] = []
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') problems.push(`console: ${message.text()}`)
  })
  await page.goto(`${stage.origin}/${role}`)
  const element = await page.waitForSelector('#deck')
  const deck = (await element.contentFrame())!
  await page.waitForFunction(() => (window as unknown as { seen: unknown[] }).seen.length > 0, undefined, { timeout: 8000 }).catch(async (error) => {
    console.info('stage did not report', JSON.stringify(problems))
    throw error
  })
  return { page, deck, problems }
}

const shownSlide = (deck: Frame) => deck.locator('.slidev-page:visible h1').first().innerText()
const seen = (page: Page) => page.evaluate(() => (window as unknown as { seen: number[][] }).seen)
const goTo = (page: Page, slide: number, click: number) =>
  page.evaluate(([s, c]: [number, number]) => (window as unknown as { goTo: (s: number, c: number) => void }).goTo(s, c), [slide, click] as [number, number])

// A message each check of the bridge must drop, one per check.
const hostileMessages = [
  { name: 'a slide above the total', slide: 99, click: 0 },
  { name: 'a slide that is not an integer', slide: 1.5, click: 0 },
  { name: 'a slide that is a string', slide: '2', click: 0 },
  { name: 'a click below zero', slide: 2, click: -1 },
  { name: 'a click above the clicks total', slide: 1, click: 50 },
]

describe('a Slidev build in a sandboxed opaque-origin frame', () => {
  it('frames the build with exactly allow-scripts', async () => {
    const { page } = await openStage('presenter')
    expect(await page.locator('#deck').getAttribute('sandbox')).toBe('allow-scripts')
  })

  it('frames the build with no permissions policy', async () => {
    const { page } = await openStage('presenter')
    expect(await page.locator('#deck').getAttribute('allow')).toBeNull()
  })

  it('gives the framed deck an opaque origin', async () => {
    const { deck } = await openStage('presenter')
    expect(await deck.evaluate(() => window.origin)).toBe('null')
  })

  it('renders the first slide in the frame', async () => {
    const { deck } = await openStage('presenter')
    await expect(shownSlide(deck)).resolves.toBe('Slide one')
  })

  it('serves module scripts in CORS mode from the token path with no cookie', async () => {
    await openStage('presenter')
    const scripts = contentRequests.filter((request) => request.path.endsWith('.js'))
    expect(scripts.length).toBeGreaterThan(0)
    expect(scripts.every((request) => request.fetchMode === 'cors')).toBe(true)
    expect(contentRequests.every((request) => request.path.startsWith(`/t/${TOKEN}/`) && !request.hasCookie)).toBe(true)
    expect((await Promise.all(contentResponseHeaders)).some((headers) => 'set-cookie' in headers)).toBe(false)
  })

  it('moves the projector window when the presenter window goes to a slide and a click', async () => {
    const presenter = await openStage('presenter')
    const projector = await openStage('projector')
    await goTo(presenter.page, 2, 0)
    await vi.waitFor(async () => expect(await shownSlide(projector.deck)).toBe('Slide two'))
    await goTo(presenter.page, 2, 1)
    await vi.waitFor(async () => expect((await seen(projector.page)).at(-1)).toEqual([2, 1]))
    await goTo(presenter.page, 3, 0)
    await vi.waitFor(async () => expect(await shownSlide(projector.deck)).toBe('Slide three'))
    expect(projector.deck.url().startsWith(`${content.origin}/t/${TOKEN}/`)).toBe(true)
  })

  it.each(hostileMessages)('drops a go-to with $name', async ({ slide, click }) => {
    const projector = await openStage('projector')
    await projector.page.evaluate((message) => {
      const frame = document.getElementById('deck') as HTMLIFrameElement
      frame.contentWindow!.postMessage({ type: 'go', ...message }, '*')
    }, { slide, click })
    await goTo(projector.page, 3, 0)
    await vi.waitFor(async () => expect((await seen(projector.page)).at(-1)).toEqual([3, 0]))
    expect(await seen(projector.page)).toEqual([[1, 0], [3, 0]])
  })

  it.each(hostileMessages)('drops a report with $name', async ({ slide, click }) => {
    const presenter = await openStage('presenter')
    const report = (message: Record<string, unknown>) =>
      presenter.deck.evaluate((state) => parent.postMessage({ type: 'state', total: 3, clicksTotal: 2, ...state }, '*'), message)
    await report({ slide, click })
    await report({ slide: 3, click: 1 })
    await vi.waitFor(async () => expect((await seen(presenter.page)).at(-1)).toEqual([3, 1]))
    expect(await seen(presenter.page)).toEqual([[1, 0], [3, 1]])
  })

  it('has no storage in the opaque origin', async () => {
    const { deck } = await openStage('presenter')
    expect(await deck.evaluate(() => (window as unknown as { blockedStorage: string[] }).blockedStorage))
      .toEqual(['localStorage', 'sessionStorage'])
  })

  // Guards Chromium's partitioning of an opaque origin, which the relay relies on.
  it('hears no BroadcastChannel message from another window', async () => {
    const presenter = await openStage('presenter')
    const projector = await openStage('projector')
    await presenter.deck.evaluate(() => {
      const w = window as unknown as { heard: unknown[] }
      w.heard = []
      const channel = new BroadcastChannel('probe')
      channel.onmessage = (event) => w.heard.push(event.data)
    })
    await projector.deck.evaluate(() => new BroadcastChannel('probe').postMessage('hello'))
    await presenter.deck.evaluate(() => new BroadcastChannel('probe').postMessage('sentinel'))
    const heard = () => presenter.deck.evaluate(() => (window as unknown as { heard: unknown[] }).heard)
    await vi.waitFor(async () => expect(await heard()).toContain('sentinel'))
    expect(await heard()).toEqual(['sentinel'])
  })

  it('raises no console error in the deck beyond the screen wake lock', async () => {
    const presenter = await openStage('presenter')
    await vi.waitFor(async () => expect(await shownSlide(presenter.deck)).toBe('Slide one'))
    // The frame is not granted the screen wake lock Slidev asks for.
    expect(presenter.problems.filter((problem) => !/wake[ -]?lock/i.test(problem))).toEqual([])
  })
})

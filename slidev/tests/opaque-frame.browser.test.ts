import { cp, mkdtemp, rm } from 'node:fs/promises'
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import type { Browser, BrowserContext, Frame, Page } from 'playwright-chromium'

import { buildStagedDeck, closeBrowser, launchBrowser } from './presenter-overlay-harness'

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
const contentResponseHeaders: Record<string, string | number | string[] | undefined>[] = []
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

async function serveBuild(request: IncomingMessage, response: ServerResponse) {
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
  const { readFile, stat } = await import('node:fs/promises')
  const relative = pathname.slice(prefix.length)
  const candidate = join(buildDirectory, relative)
  const isAsset = relative.includes('.') && candidate.startsWith(`${buildDirectory}/`)
  const file = isAsset && (await stat(candidate).catch(() => null))?.isFile() ? candidate : join(buildDirectory, 'index.html')
  const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream'
  const headers = { 'content-type': type, 'access-control-allow-origin': '*' }
  contentResponseHeaders.push(headers)
  response.writeHead(200, headers)
  response.end(await readFile(file))
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
})

afterAll(async () => {
  await closeBrowser(browser)
  content.server.close()
  stage.server.close()
  await rm(deckDirectory, { recursive: true, force: true })
  await rm(buildDirectory, { recursive: true, force: true })
})

type Opened = { page: Page, deck: Frame, problems: string[] }

async function openStage(role: 'presenter' | 'projector'): Promise<Opened> {
  const page = await context.newPage()
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

describe('a Slidev build in a sandboxed opaque-origin frame', () => {
  it('frames with exactly allow-scripts, no permissions, and an opaque origin', async () => {
    const { page, deck } = await openStage('presenter')
    const frameElement = page.locator('#deck')
    expect(await frameElement.getAttribute('sandbox')).toBe(SANDBOX)
    expect(await frameElement.getAttribute('allow')).toBeNull()
    expect(await deck.evaluate(() => window.origin)).toBe('null')
    await expect(shownSlide(deck)).resolves.toBe('Slide one')
    await page.close()
  })

  it('serves module scripts in CORS mode from the token path with no cookie', async () => {
    const { page } = await openStage('presenter')
    const scripts = contentRequests.filter((request) => request.path.endsWith('.js'))
    expect(scripts.length).toBeGreaterThan(0)
    expect(scripts.every((request) => request.fetchMode === 'cors')).toBe(true)
    expect(contentRequests.every((request) => request.path.startsWith(`/t/${TOKEN}/`) && !request.hasCookie)).toBe(true)
    expect(contentResponseHeaders.some((headers) => 'set-cookie' in headers)).toBe(false)
    await page.close()
  })

  it('moves the projector window when the presenter window goes to a slide and a click', async () => {
    const presenter = await openStage('presenter')
    const projector = await openStage('projector')
    await presenter.page.evaluate(() => (window as unknown as { goTo: (s: number, c: number) => void }).goTo(2, 0))
    await vi.waitFor(async () => expect(await shownSlide(projector.deck)).toBe('Slide two'))
    await presenter.page.evaluate(() => (window as unknown as { goTo: (s: number, c: number) => void }).goTo(2, 1))
    await vi.waitFor(async () => expect((await seen(projector.page)).at(-1)).toEqual([2, 1]))
    await presenter.page.evaluate(() => (window as unknown as { goTo: (s: number, c: number) => void }).goTo(3, 0))
    await vi.waitFor(async () => expect(await shownSlide(projector.deck)).toBe('Slide three'))
    expect(projector.deck.url().startsWith(`${content.origin}/t/${TOKEN}/`)).toBe(true)
    await presenter.page.close()
    await projector.page.close()
  })

  it('drops a go-to or a report that is not an integer inside the deck', async () => {
    const presenter = await openStage('presenter')
    const projector = await openStage('projector')
    for (const bad of [{ slide: 99, click: 0 }, { slide: 1.5, click: 0 }, { slide: '2', click: 0 }, { slide: 2, click: -1 }, { slide: 1, click: 50 }]) {
      await projector.page.evaluate((message) => {
        const frame = document.getElementById('deck') as HTMLIFrameElement
        frame.contentWindow!.postMessage({ type: 'go', ...message }, '*')
      }, bad)
      await presenter.deck.evaluate((message) => parent.postMessage({ type: 'state', total: 3, ...message }, '*'), bad)
    }
    await presenter.page.waitForTimeout(500)
    expect(await shownSlide(projector.deck)).toBe('Slide one')
    expect(await seen(projector.page)).toEqual([[1, 0]])
    expect(await seen(presenter.page)).toEqual([[1, 0]])
    await presenter.page.close()
    await projector.page.close()
  })

  it('blocks storage and a cross-window BroadcastChannel in the opaque origin, and the shim and page relay cover both', async () => {
    const presenter = await openStage('presenter')
    const projector = await openStage('projector')
    expect(await presenter.deck.evaluate(() => (window as unknown as { blockedStorage: string[] }).blockedStorage))
      .toEqual(['localStorage', 'sessionStorage'])
    await presenter.deck.evaluate(() => {
      const w = window as unknown as { heard: unknown[] }
      w.heard = []
      const channel = new BroadcastChannel('probe')
      channel.onmessage = (event) => w.heard.push(event.data)
    })
    await projector.deck.evaluate(() => new BroadcastChannel('probe').postMessage('hello'))
    await presenter.page.waitForTimeout(500)
    expect(await presenter.deck.evaluate(() => (window as unknown as { heard: unknown[] }).heard)).toEqual([])
    // The deck's only complaint is Slidev asking for the screen wake lock the frame is not granted.
    expect(presenter.problems.filter((problem) => !/wake[ -]?lock/i.test(problem))).toEqual([])
    await presenter.page.close()
    await projector.page.close()
  })
})

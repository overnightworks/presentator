import { createHash } from 'node:crypto'
import { createServer, type ServerResponse } from 'node:http'
import { copyFile, mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

import { chromium, type ElementHandle, type Page } from 'playwright-chromium'
import { afterAll, afterEach, beforeAll, beforeEach, expect, inject } from 'vitest'

const run = promisify(execFile)
const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url))
const frontendRoot = join(repositoryRoot, 'frontend')
export const overlayDeckSourceDirectory = join(repositoryRoot, 'examples/copresenter-deck')
const evidenceDirectory = process.env.PRESENTER_RETURN_EVIDENCE_DIR
const wav = 'UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA='
// Exactly what `examples/copresenter-deck/README.md` tells an operator to
// copy next to their own `slides.md` — proven by assembling a fresh deck from
// only these files, never the repository's own example directory, where
// every other file already happens to sit beside them regardless of whether
// the README's own list is complete.
// What the server answers before any stream starts: F1's refusals, plus the
// two shapes it never sends, a 403 and a 401 that is not JSON.
const REFUSED_REQUESTS = {
  'signed-out-refresh': { status: 401, contentType: 'application/json', body: '{"detail":"Sign in again"}' },
  'no-session': { status: 401, contentType: 'application/json', body: '{"detail":"Not authenticated"}' },
  'forbidden': { status: 403, contentType: 'application/json', body: '{"detail":"Missing Origin header"}' },
  'unavailable': { status: 503, contentType: 'application/json', body: '{"detail":"Co-presenter unavailable"}' },
  'unreadable-401': { status: 401, contentType: 'text/plain', body: 'Sign in again' },
} as const

const COPIED_OVERLAY_FILES = [
  'custom-nav-controls.vue',
  'style.css',
  'components/CoPresenter.vue',
  'public/pcm-capture-processor.js',
]


let buildDirectory = ''
let server: ReturnType<typeof createServer>
let productionSecurityHeaders: Record<string, string> = {}
type CspViolation = { directive: string; blockedUri: string }
let cspViolations: CspViolation[] = []
const hearingSockets = new Set<import('node:stream').Duplex>()

export type AnswerMode =
  | 'audio'
  | 'speech-refused'
  | 'not-enabled'
  | 'unknown-reason'
  | 'mid-answer-error'
  | 'refused-then-answered'
  | keyof typeof REFUSED_REQUESTS

type MediaObservation = {
  microphoneOpened: boolean
  trackStopped: boolean
  playbackStarted: boolean
  playbackPaused: boolean
}

// What a test reads or sets while the fake server runs; each test file that
// imports this harness gets its own state and its own server.
export const state = {
  origin: '',
  hearingOpened: 0,
  hearingClosed: 0,
  pendingAnswerClosed: 0,
  askHeaderNames: [] as string[],
  answerMode: 'audio' as AnswerMode,
  askBodies: [] as { said: string, deck: string }[],
  hearingMode: 'ready' as 'ready' | 'not-ready' | 'socket-error' | 'unreachable',
  media: createMediaObservation(),
}

function frame(message: string) {
  const data = Buffer.from(message)
  return Buffer.concat([Buffer.from([0x81, data.length]), data])
}

function mimeType(pathname: string) {
  return {
    '.css': 'text/css',
    '.html': 'text/html',
    '.js': 'text/javascript',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
  }[extname(pathname)] ?? 'application/octet-stream'
}

// What `SecurityHeadersPolicy` really sends in production on the talk host,
// where a talk runs: the policy `presentator.api.talk.talk_content_security_policy`
// builds, read through this one bridge call and never hand-copied here, so
// a changed policy fails this test's own build rather than silently drifting
// from what a browser reaching the real deployment gets.
const PYTHON_SECURITY_HEADERS_SCRIPT = `
import json
from presentator.api.talk import talk_content_security_policy
print(json.dumps({"content-security-policy": talk_content_security_policy()}))
`

async function loadProductionSecurityHeaders(): Promise<Record<string, string>> {
  const { stdout } = await run('uv', ['run', 'python', '-c', PYTHON_SECURITY_HEADERS_SCRIPT], {
    cwd: repositoryRoot,
  })
  return JSON.parse(stdout) as Record<string, string>
}

export function boxesIntersect(first: { x: number, y: number, width: number, height: number }, second: { x: number, y: number, width: number, height: number }) {
  return first.x < second.x + second.width
    && second.x < first.x + first.width
    && first.y < second.y + second.height
    && second.y < first.y + first.height
}

export async function readSameFrameBoxes(page: Page, homeHandle: ElementHandle<Element>, aiHandle: ElementHandle<Element>) {
  return page.evaluate(([home, ai]) => {
    const rectOf = (element: Element) => {
      const { x, y, width, height } = element.getBoundingClientRect()
      return { x, y, width, height }
    }
    return {
      homeBox: rectOf(home),
      aiBox: rectOf(ai),
      navWidth: home.parentElement!.getBoundingClientRect().width,
    }
  }, [homeHandle, aiHandle] as const)
}

export async function saveEvidence(page: Page, name: string) {
  if (!evidenceDirectory) return
  await page.screenshot({ path: join(evidenceDirectory, name), fullPage: true })
}

// Builds a second, independent deck containing only the files
// `examples/copresenter-deck/README.md` names, plus the minimal `slides.md`
// every deck needs (the one file that is the operator's own, never copied).
export async function buildCopiedOverlayDeck(base = '/'): Promise<string> {
  const deckDirectory = await mkdtemp(join(tmpdir(), 'presenter-copied-overlay-deck-'))
  for (const relativePath of COPIED_OVERLAY_FILES) {
    const destination = join(deckDirectory, relativePath)
    await mkdir(dirname(destination), { recursive: true })
    await copyFile(join(overlayDeckSourceDirectory, relativePath), destination)
  }
  await writeFile(
    join(deckDirectory, 'slides.md'),
    '---\ntitle: Copied overlay\nfonts:\n  provider: none\nfavicon: false\n---\n\n# Copied overlay\n',
  )
  const outDirectory = await mkdtemp(join(tmpdir(), 'presenter-copied-overlay-build-'))
  await run(
    'pnpm',
    ['exec', 'slidev', 'build', join(deckDirectory, 'slides.md'), '--out', outDirectory, '--base', base],
    { cwd: frontendRoot },
  )
  await rm(deckDirectory, { recursive: true, force: true })
  return outDirectory
}

async function serveFile(directory: string, pathname: string, response: ServerResponse) {
  const requested = normalize(resolve(directory, `.${pathname}`))
  const candidate = requested.startsWith(`${directory}/`) ? requested : ''
  try {
    const info = candidate ? await stat(candidate) : null
    if (info?.isFile()) {
      response.writeHead(200, { 'content-type': mimeType(candidate), ...productionSecurityHeaders })
      response.end(await readFile(candidate))
      return
    }
  } catch {
    // The SPA fallback below owns client-side routes.
  }
  response.writeHead(200, { 'content-type': 'text/html', ...productionSecurityHeaders })
  response.end(await readFile(join(directory, 'index.html')))
}

// A peer reset while a page tears down is expected on an upgraded socket and
// would otherwise surface as an unhandled `error` that fails the whole run;
// any other socket error is kept for the shared `afterEach` to judge.
const socketErrors: Error[] = []

function recordUnexpectedSocketErrors(socket: import('node:stream').Duplex) {
  socket.on('error', (error: NodeJS.ErrnoException) => {
    if (error.code !== 'ECONNRESET') socketErrors.push(error)
  })
}

function websocketAcceptKey(key: string) {
  return createHash('sha1').update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64')
}

// The minimal server the copied-overlay proof needs: the static build plus
// the two routes `startLocalHear`'s own path calls before it ever reaches the
// AudioWorklet (`/who`, then the hearing lease). `/ask` only reports what it
// was asked, and there are no observation beacons: no proof here drives a
// spoken turn. `basePath` serves the build below the folder a deck lives in.
export async function createOverlayDeckServer(
  directory: string,
  { basePath = '', onAsk = () => undefined }: { basePath?: string, onAsk?: (said: string, deck: string) => void } = {},
) {
  // `closeAllConnections` does not reach an upgraded (WebSocket) connection
  // (Node's own documented behaviour) — the same reason `startServer` below
  // tracks `hearingSockets` and destroys them itself before closing, which
  // this server has to do too or its own `close()` never calls back.
  const sockets = new Set<import('node:stream').Duplex>()
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://presenter.test')
    if (url.pathname === '/copresenter/who') {
      response.writeHead(200, { 'content-type': 'application/json' })
      response.end(
        JSON.stringify({
          speech: { sample_rate: 16000, hearing: { ready: true } },
        }),
      )
      return
    }
    if (url.pathname === '/copresenter/ask') {
      const { said, deck } = await readAskBody(request)
      onAsk(said, deck)
      response.writeHead(200, { 'content-type': 'text/event-stream' })
      sendEvent(response, 'done', { text: 'Answered.' })
      response.end()
      return
    }
    // Vite writes the base percent-encoded into scripts but raw into the HTML.
    const prefix = [basePath, basePath.replaceAll('%25', '%')].find((base) => base && url.pathname.startsWith(base))
    await serveFile(directory, prefix ? url.pathname.slice(prefix.length) || '/' : url.pathname, response)
  })
  server.on('upgrade', (request, socket) => {
    const key = request.headers['sec-websocket-key']
    if (!request.url?.startsWith('/copresenter/hear') || typeof key !== 'string') {
      socket.destroy()
      return
    }
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
      + `Sec-WebSocket-Accept: ${websocketAcceptKey(key)}\r\n\r\n`,
    )
    recordUnexpectedSocketErrors(socket)
    sockets.add(socket)
    socket.on('close', () => sockets.delete(socket))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (!address || typeof address === 'string') {
    throw new Error('overlay deck server did not bind a TCP port')
  }
  return { server, origin: `http://127.0.0.1:${address.port}`, sockets }
}

async function startServer() {
  server = createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://presenter.test')
    if (url.pathname === '/copresenter/who') {
      if (state.hearingMode === 'unreachable') {
        response.writeHead(503, { 'content-type': 'application/json' })
        response.end(JSON.stringify({ detail: 'Co-presenter unavailable' }))
        return
      }
      response.writeHead(200, { 'content-type': 'application/json' })
      response.end(JSON.stringify({ speech: { sample_rate: 16000, hearing: { ready: state.hearingMode !== 'not-ready' } } }))
      return
    }
    if (url.pathname === '/copresenter/ask') {
      state.askHeaderNames = Object.keys(request.headers)
      state.askBodies.push(await readAskBody(request))
      const refusal = REFUSED_REQUESTS[state.answerMode as keyof typeof REFUSED_REQUESTS]
      if (refusal) {
        response.writeHead(refusal.status, { 'content-type': refusal.contentType })
        response.end(refusal.body)
        return
      }
      response.writeHead(200, { 'content-type': 'text/event-stream' })
      const refusedFirst = state.answerMode === 'refused-then-answered' && state.askBodies.length === 1
      if (refusedFirst) {
        response.end('event: error\ndata: {}\n\n')
        return
      }
      if (state.answerMode === 'refused-then-answered') {
        sendEvent(response, 'done', { text: 'The second ask is answered.' })
        response.end()
        return
      }
      if (state.answerMode === 'not-enabled' || state.answerMode === 'unknown-reason') {
        sendEvent(response, 'error', {
          reason: state.answerMode,
          message: 'the answer could not be spoken',
        })
        response.end()
        return
      }
      if (state.answerMode === 'mid-answer-error') {
        sendEvent(response, 'text', { text: 'The first sentence is voiced. Then the second one fails.' })
        sendEvent(response, 'sentence', { text: 'The first sentence is voiced.' })
        sendEvent(response, 'audio', { wav_b64: wav })
        sendEvent(response, 'sentence', { text: 'Then the second one fails.' })
        sendEvent(response, 'error', {})
        response.end()
        return
      }
      if (state.answerMode === 'speech-refused') {
        const sentence = 'The handoff keeps this sentence.'
        sendEvent(response, 'text', { text: sentence })
        sendEvent(response, 'sentence', { text: sentence })
        response.end('event: error\ndata: {}\n\n')
        return
      }
      response.write(`event: audio\ndata: {"wav_b64":"${wav}"}\n\n`)
      response.on('close', () => { state.pendingAnswerClosed += 1 })
      return
    }
    if (request.method === 'POST' && url.pathname.startsWith('/observations/')) {
      if (url.pathname === '/observations/microphone-opened') state.media.microphoneOpened = true
      if (url.pathname === '/observations/track-stopped') state.media.trackStopped = true
      if (url.pathname === '/observations/playback-started') state.media.playbackStarted = true
      if (url.pathname === '/observations/playback-paused') state.media.playbackPaused = true
      response.writeHead(204)
      response.end()
      return
    }
    await serveFile(buildDirectory, url.pathname, response)
  })
  server.on('upgrade', (request, socket) => {
    if (!request.url?.startsWith('/copresenter/hear')) {
      socket.destroy()
      return
    }
    const key = request.headers['sec-websocket-key']
    if (typeof key !== 'string') {
      socket.destroy()
      return
    }
    state.hearingOpened += 1
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
      + `Sec-WebSocket-Accept: ${websocketAcceptKey(key)}\r\n\r\n`,
    )
    if (state.hearingMode === 'ready') {
      socket.write(frame(JSON.stringify({ text: 'Where does the handoff slow down?', final: true })))
    }
    recordUnexpectedSocketErrors(socket)
    hearingSockets.add(socket)
    let closed = false
    const closeHearing = () => {
      if (closed) return
      closed = true
      hearingSockets.delete(socket)
      state.hearingClosed += 1
    }
    socket.on('data', (data) => {
      if ((data[0] & 0x0f) !== 0x08) return
      socket.end(Buffer.from([0x88, 0x00]))
    })
    socket.on('close', () => {
      closeHearing()
    })
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('test server did not bind a TCP port')
  state.origin = `http://127.0.0.1:${address.port}`
}

function sendEvent(response: ServerResponse, event: string, data: object) {
  response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
}

async function readAskBody(request: import('node:http').IncomingMessage) {
  const chunks: Buffer[] = []
  for await (const chunk of request) chunks.push(chunk as Buffer)
  return JSON.parse(Buffer.concat(chunks).toString('utf8')) as { said: string, deck: string }
}

export function sendHeard(text: string) {
  for (const socket of hearingSockets) socket.write(frame(JSON.stringify({ text, final: true })))
}

export function sendHearingError() {
  for (const socket of hearingSockets) socket.write(frame(JSON.stringify({ error: 'hearing failed' })))
}

export function createMediaObservation(): MediaObservation {
  return {
    microphoneOpened: false,
    trackStopped: false,
    playbackStarted: false,
    playbackPaused: false,
  }
}

async function installMediaFakes(page: Page) {
  await page.addInitScript(() => {
    const report = (event: string) => {
      navigator.sendBeacon(`/observations/${event}`)
    }
    const track = {
      readyState: 'live',
      stop() {
        this.readyState = 'ended'
        report('track-stopped')
      },
    }
    const microphones = window as unknown as { __microphonesRequested: number }
    microphones.__microphonesRequested = 0
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: async () => {
          microphones.__microphonesRequested += 1
          report('microphone-opened')
          return { getTracks: () => [track] }
        },
      },
      configurable: true,
    })
    const RealAudioContext = window.AudioContext
    class FakeContext {
      sampleRate = 16000
      audioWorklet = {
        // Real, not faked: this is the one call the page's own CSP governs
        // (`script-src 'self'`), so delegating to a real, unconnected
        // `AudioContext`'s real `audioWorklet` proves the same-origin URL the
        // component names actually loads under that policy. Everything past
        // this point stays fake, because the component's own media graph
        // never has to be real for these tests to drive it.
        addModule: (moduleUrl: string) => new RealAudioContext().audioWorklet.addModule(moduleUrl),
      }
      createMediaStreamSource() { return { connect: () => undefined } }
      close() { return Promise.resolve() }
    }
    window.AudioContext = FakeContext as unknown as typeof AudioContext
    window.AudioWorkletNode = class {
      port = { onmessage: null }
      disconnect() {}
    } as unknown as typeof AudioWorkletNode
    const counter = window as unknown as { __speechRecognitionsBuilt: number }
    counter.__speechRecognitionsBuilt = 0
    class CountedSpeechRecognition {
      constructor() { counter.__speechRecognitionsBuilt += 1 }
      start() {}
      stop() {}
    }
    Object.assign(window, {
      SpeechRecognition: CountedSpeechRecognition,
      webkitSpeechRecognition: CountedSpeechRecognition,
    })
    HTMLMediaElement.prototype.play = async function () {
      Object.defineProperty(this, 'paused', { value: false, configurable: true })
      report('playback-started')
    }
    HTMLMediaElement.prototype.pause = function () {
      Object.defineProperty(this, 'paused', { value: true, configurable: true })
      report('playback-paused')
    }
  })
}

// Records every violation for the shared `afterEach` to judge: none is
// tolerated, so a page this built talk
// serves never silently drifts from what `script-src 'self'` and the rest of
// the production policy actually admit.
export async function installCspGuard(page: Page) {
  await page.exposeFunction('__reportCspViolation', (directive: string, blockedUri: string) => {
    cspViolations.push({ directive, blockedUri })
  })
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (event) => {
      const reportViolation = (window as unknown as {
        __reportCspViolation: (directive: string, blockedUri: string) => void
      }).__reportCspViolation
      // `effectiveDirective` is the spec-recommended field (CSP3): unlike the
      // legacy `violatedDirective`, it never carries the matched source list,
      // so it compares equal to a bare directive name like 'img-src'.
      reportViolation(event.effectiveDirective, event.blockedURI)
    })
  })
}

export async function openPresenterAt(
  base: string,
  path: string,
  width: number,
  prepare: (page: Page) => Promise<void> = async () => undefined,
) {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width, height: 900 } })
  page.setDefaultTimeout(5_000)
  await installCspGuard(page)
  await installMediaFakes(page)
  await prepare(page)
  await page.goto(`${base}${path}`, { waitUntil: 'domcontentloaded', timeout: 10_000 })
  return { browser, page }
}

export async function openPresenter(width: number) {
  return openPresenterAt(state.origin, '/presenter/1', width)
}


// Registers the build, the fake server and the shared per-test checks in the
// test file that calls it.
export function registerOverlayHarness() {
  beforeAll(async () => {
    buildDirectory = inject('overlayExampleBuildDirectory')
    productionSecurityHeaders = await loadProductionSecurityHeaders()
    await startServer()
  }, 60_000)

  afterAll(async () => {
    for (const socket of hearingSockets) socket.destroy()
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  })

  beforeEach(() => {
    cspViolations = []
    socketErrors.length = 0
    state.askBodies = []
  })

  afterEach(() => {
    expect(cspViolations).toEqual([])
    expect(socketErrors).toEqual([])
  })
}

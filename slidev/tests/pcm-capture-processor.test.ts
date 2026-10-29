import { afterEach, describe, expect, it, vi } from 'vitest'

// The worklet runs in the browser's audio thread, so the test supplies the two
// globals that thread provides and drives the registered processor directly.
async function loadProcessor() {
  const posted: Float32Array[] = []
  const registered: Record<string, new () => { process: (inputs: Float32Array[][]) => boolean }> = {}
  vi.stubGlobal('AudioWorkletProcessor', class {
    port = { postMessage: (message: Float32Array) => posted.push(message) }
  })
  vi.stubGlobal('registerProcessor', (name: string, processor: (typeof registered)[string]) => {
    registered[name] = processor
  })
  vi.resetModules()
  // @ts-expect-error the worklet is plain browser JavaScript without declarations
  await import('../worklets/pcm-capture-processor.js')
  return { processor: new registered['pcm-capture']!(), posted }
}

describe('the pcm-capture worklet processor', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('posts a copy of the first channel and keeps running', async () => {
    const { processor, posted } = await loadProcessor()
    const channel = new Float32Array([0.25, -0.5])

    expect(processor.process([[channel]])).toBe(true)

    expect(posted).toEqual([new Float32Array([0.25, -0.5])])
    expect(posted[0]).not.toBe(channel)
  })

  it.each([[[]], [[[]]]])('posts nothing and keeps running when the input %j has no channel', async (inputs) => {
    const { processor, posted } = await loadProcessor()

    expect(processor.process(inputs)).toBe(true)

    expect(posted).toEqual([])
  })
})

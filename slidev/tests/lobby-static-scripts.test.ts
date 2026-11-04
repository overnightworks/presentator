// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

async function runScript(script: string, markup: string) {
  document.body.innerHTML = markup
  vi.resetModules()
  await import(`../../src/presentator/api/static/${script}.js`)
}

function element<T extends HTMLElement>(selector: string) {
  const found = document.querySelector<T>(selector)
  if (!found) throw new Error(`missing ${selector}`)
  return found
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('copy-value.js', () => {
  const markup = (value: string) => `<p><code>${value}</code><button type="button" data-copy>Copy</button></p>`

  function stubClipboard(writeText: (value: string) => Promise<void>) {
    const clipboard = { writeText: vi.fn(writeText) }
    vi.stubGlobal('navigator', { clipboard })
    return clipboard
  }

  it('copies the value shown before the button', async () => {
    const clipboard = stubClipboard(async () => {})
    await runScript('copy-value', markup('ssh-ed25519 AAAA deploy'))

    element('[data-copy]').click()

    expect(clipboard.writeText).toHaveBeenCalledWith('ssh-ed25519 AAAA deploy')
  })

  it('copies nothing when there is no value', async () => {
    const clipboard = stubClipboard(async () => {})
    await runScript('copy-value', markup(''))

    element('[data-copy]').click()

    expect(clipboard.writeText).not.toHaveBeenCalled()
  })

  it('reports a refused clipboard write instead of dropping it', async () => {
    const refusal = new Error('clipboard denied')
    stubClipboard(async () => { throw refusal })
    const report = vi.spyOn(console, 'error').mockImplementation(() => {})
    await runScript('copy-value', markup('value'))

    element('[data-copy]').click()

    await vi.waitFor(() => expect(report).toHaveBeenCalledWith('Copying to the clipboard failed', refusal))
  })
})

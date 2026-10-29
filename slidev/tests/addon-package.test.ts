import { createHash } from 'node:crypto'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { addonRootDirectory, buildStagedDeck, stageAddonDeck } from './presenter-overlay-harness'

const sha256 = (content: Buffer) => createHash('sha256').update(content).digest('hex')

describe('the presentator addon package', () => {
  it('serves the overlay to a deck staged outside the toolchain that names the addon', async () => {
    // Slidev refuses an addon whose `engines.slidev` the running CLI is outside
    // of, so a successful build also proves the declared range still holds.
    const deckDirectory = await stageAddonDeck()
    const outDirectory = await mkdtemp(join(tmpdir(), 'presenter-addon-package-build-'))
    try {
      expect(await readdir(deckDirectory)).toEqual(['slides.md'])

      await buildStagedDeck(deckDirectory, outDirectory)

      const emittedWorklets = (await readdir(join(outDirectory, 'assets')))
        .filter((name) => /^pcm-capture-processor-.*\.js$/.test(name))
      expect(emittedWorklets).toHaveLength(1)
      const emittedWorklet = await readFile(join(outDirectory, 'assets', ...emittedWorklets))
      const addonWorklet = await readFile(join(addonRootDirectory, 'worklets/pcm-capture-processor.js'))
      expect(sha256(emittedWorklet)).toBe(sha256(addonWorklet))

      const stagedEntries = await readdir(deckDirectory)
      for (const copiedSource of ['custom-nav-controls.vue', 'style.css', 'components', 'worklets']) {
        expect(stagedEntries).not.toContain(copiedSource)
      }
    } finally {
      await rm(deckDirectory, { recursive: true, force: true })
      await rm(outDirectory, { recursive: true, force: true })
    }
  }, 120_000)
})

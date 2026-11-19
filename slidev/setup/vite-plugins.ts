import { relative } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Instrumenter } from 'istanbul-lib-instrument'

// Only the overlay's browser tests build an instrumented deck (`pnpm test` sets
// OVERLAY_INSTRUMENTED); a deck the image builds never carries counters, and
// the instrumenter is not even loaded there. Slidev reads this setup file, not
// a `vite.config.ts`: Vite would write a bundled config next to the addon,
// which the image's read-only root refuses.
const addonRoot = fileURLToPath(new URL('..', import.meta.url))
const overlaySources = /^(components\/.+\.vue|custom-nav-controls\.vue|global-top\.vue|room-notice\.js|controls-words\.js)$/
// Slidev inserts its context into every component and returns no source map
// for it, which leaves the build without one for the component.
const contextInjection = 'slidev:context-injection'

type SourceMap = Parameters<Instrumenter['instrumentSync']>[2]
type TransformContext = { getCombinedSourcemap: () => SourceMap }
type TransformHandler = (this: unknown, code: string, id: string, ...rest: unknown[]) => unknown
type BuildPlugin = { name: string, transform?: { handler: TransformHandler } }

function isOverlaySource(id: string) {
  const [filename = '', query = ''] = id.split('?')
  // A component's counters belong to its compiled script, which also holds
  // the template's render code; its style parts hold no code.
  return overlaySources.test(relative(addonRoot, filename)) && (!query || query.includes('type=script'))
}

// Gives Slidev's insertion the source map it lacks, so the component's
// counters land on its own lines.
async function mapContextInjection(plugins: readonly BuildPlugin[]) {
  const { default: MagicString } = await import('magic-string')
  const injection = plugins.find((plugin) => plugin.name === contextInjection)?.transform
  if (!injection) throw new Error(`${contextInjection} no longer transforms components`)
  const inject = injection.handler
  injection.handler = async function (code, id, ...rest) {
    const injected = await inject.call(this, code, id, ...rest)
    if (typeof injected !== 'string' || !isOverlaySource(id)) return injected
    let prefix = 0
    while (prefix < code.length && code[prefix] === injected[prefix]) prefix += 1
    const inserted = injected.slice(prefix, prefix + injected.length - code.length)
    if (`${code.slice(0, prefix)}${inserted}${code.slice(prefix)}` !== injected) {
      throw new Error(`${contextInjection} changed ${id} beyond an insertion`)
    }
    const mapped = new MagicString(code).appendLeft(prefix, inserted)
    return { code: injected, map: mapped.generateMap({ hires: true, source: id }) }
  }
}

export default async function overlayCoverage() {
  if (!process.env.OVERLAY_INSTRUMENTED) return []
  const { createInstrumenter } = await import('istanbul-lib-instrument')
  const instrumenter = createInstrumenter({
    coverageGlobalScope: 'globalThis',
    coverageGlobalScopeFunc: false,
    esModules: true,
    produceSourceMap: true,
  })
  return [{
    name: 'presentator:overlay-coverage',
    // Vue writes its source maps only for a build that keeps them.
    config: () => ({ build: { sourcemap: 'hidden' as const } }),
    configResolved: (config: { plugins: readonly BuildPlugin[] }) => mapContextInjection(config.plugins),
    transform(this: TransformContext, code: string, id: string) {
      if (!isOverlaySource(id)) return undefined
      const instrumented = instrumenter.instrumentSync(code, id.split('?')[0] ?? id, this.getCombinedSourcemap())
      return { code: instrumented, map: instrumenter.lastSourceMap() }
    },
  }]
}

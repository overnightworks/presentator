// Adds the counters the overlay's browser tests read from the instrumented
// deck to vitest's on the same source files, and writes the overlay's lcov
// file for SonarCloud at the repository root's paths. The merged figure is
// reported, not checked: vitest's own thresholds stay the gate.
import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import libCoverage, { type CoverageMapData } from 'istanbul-lib-coverage'
import libReport from 'istanbul-lib-report'
import libSourceMaps from 'istanbul-lib-source-maps'
import reports from 'istanbul-reports'

const reportsDirectory = fileURLToPath(new URL('../../reports/slidev-coverage', import.meta.url))
const browserDirectory = fileURLToPath(new URL('../../reports/slidev-browser-coverage', import.meta.url))
const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url))

function read(path: string): CoverageMapData {
  return JSON.parse(readFileSync(path, 'utf8')) as CoverageMapData
}

// The instrumented deck counts positions in the code the build made; the
// source map each file carries puts them back on the source files vitest
// reports.
const browser = libCoverage.createCoverageMap({})
for (const file of readdirSync(browserDirectory)) {
  browser.merge(read(`${browserDirectory}/${file}`))
}
const merged = libCoverage.createCoverageMap(read(`${reportsDirectory}/coverage-final.json`))
const remapped = await libSourceMaps.createSourceMapStore().transformCoverage(browser)
// Remapping ends a line's last range at Infinity, which vitest's JSON file
// holds as null; written the same way, equal ranges are counted as one.
merged.merge(libCoverage.createCoverageMap(JSON.parse(JSON.stringify(remapped)) as CoverageMapData))

const context = libReport.createContext({ dir: reportsDirectory, coverageMap: merged })
reports.create('lcovonly', { file: 'lcov.info', projectRoot: repositoryRoot }).execute(context)
reports.create('text').execute(context)

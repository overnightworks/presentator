import libCoverage from 'istanbul-lib-coverage'
import { createInstrumenter } from 'istanbul-lib-instrument'
import libSourceMaps from 'istanbul-lib-source-maps'
import { expect, test } from 'vitest'
import { compileScript, parse } from 'vue/compiler-sfc'

import { placeTemplateArmsOnSource } from '../scripts/template-branches'

const filename = '/component.vue'
const component = [
  '<script setup>',
  "const props = defineProps({ shown: Boolean, label: { type: String, default: '' } })",
  "const text = props.label || 'none'",
  "const mark = props.shown ? '!' : ''",
  '</script>',
  '',
  '<template>',
  '  <p',
  '    v-if="props.shown"',
  '    class="notice"',
  '  >',
  '    <span v-text="text + mark" />',
  '  </p>',
  '</template>',
  '',
].join('\n')

function lineOf(fragment: string) {
  return component.split('\n').findIndex((line) => line.includes(fragment)) + 1
}

// Builds the component the way the deck's build does (its template inlined
// into the script) and counts its branches on the component's own lines.
async function countedBranches() {
  const { descriptor } = parse(component, { filename, sourceMap: true })
  const script = compileScript(descriptor, { id: 'component', inlineTemplate: true, sourceMap: true })
  if (!script.map) throw new Error('the compiled component carries no source map')
  const instrumenter = createInstrumenter({ esModules: true, produceSourceMap: true })
  instrumenter.instrumentSync(script.content, filename, script.map)
  const instrumented = { ...instrumenter.lastFileCoverage(), inputSourceMap: JSON.stringify(script.map) }
  const counted = placeTemplateArmsOnSource({ [filename]: instrumented })
  const remapped = await libSourceMaps.createSourceMapStore().transformCoverage(libCoverage.createCoverageMap(counted))
  return Object.values(remapped.fileCoverageFor(filename).branchMap).map((branch) => ({
    line: branch.loc.start.line,
    arms: branch.locations.length,
  }))
}

test('every condition of a component, its template v-if included, is counted on its line with both arms', async () => {
  const expected = ['||', ' ? ', 'v-if='].map((fragment) => ({ line: lineOf(fragment), arms: 2 }))
  expect(await countedBranches()).toEqual(expected)
})

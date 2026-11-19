import { decodedMappings, type SourceMapInput, TraceMap } from '@jridgewell/trace-mapping'
import type { FileCoverageData, Location, Range } from 'istanbul-lib-coverage'

// The instrumented deck passes every source's map to the instrumenter, which
// keeps it beside the counters.
export type InstrumentedCoverage = Record<string, FileCoverageData & { inputSourceMap: SourceMapInput }>

// Vue maps a template's render code only where it writes an expression or an
// element; the code around them (a call's opening, the comment a false v-if
// leaves) carries no source position. Remapping drops every branch arm whose
// ends sit on a line without one, which drops a v-if's condition whole. Each
// such arm is narrowed to the mapped code it holds, and an arm holding none is
// placed on its condition, the code before the first arm. A branch without
// mapped code there (an if statement, an operator, Vue's render cache) is
// left as it is.
export function placeTemplateArmsOnSource(coverage: InstrumentedCoverage): InstrumentedCoverage {
  for (const fileData of Object.values(coverage)) {
    const positions = mappedPositions(new TraceMap(fileData.inputSourceMap))
    const mappedLines = new Set(positions.map((position) => position.line))
    const onSource = (range: Range) => mappedLines.has(range.start.line) && mappedLines.has(range.end.line)
    for (const branch of Object.values(fileData.branchMap)) {
      const [firstArm] = branch.locations as [Range, ...Range[]]
      const condition = mappedSpan(positions, { start: branch.loc.start, end: firstArm.start })
      if (!condition) continue
      const placed = (range: Range) => (onSource(range) ? range : (mappedSpan(positions, range) ?? condition))
      branch.loc = placed(branch.loc)
      branch.locations = branch.locations.map(placed)
    }
  }
  return coverage
}

// The generated positions that carry a source position, in istanbul's 1-based
// lines and in order.
function mappedPositions(sourceMap: TraceMap): Location[] {
  return decodedMappings(sourceMap).flatMap((segments, index) =>
    segments.filter((segment) => segment.length > 1).map(([column]) => ({ line: index + 1, column })),
  )
}

function precedes(position: Location, other: Location) {
  return position.line < other.line || (position.line === other.line && position.column < other.column)
}

function mappedSpan(positions: readonly Location[], range: Range): Range | undefined {
  const inside = positions.filter((position) => !precedes(position, range.start) && precedes(position, range.end))
  const first = inside[0]
  const last = inside.at(-1)
  if (!first || !last) return undefined
  return { start: first, end: { line: last.line, column: last.column + 1 } }
}

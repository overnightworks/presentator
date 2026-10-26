import { execFile } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

import type { TestProject } from 'vitest/node'

declare module 'vitest' {
  export interface ProvidedContext {
    overlayExampleBuildDirectory: string
  }
}

const run = promisify(execFile)
const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url))
const providedBuild = process.env.PRESENTER_RETURN_BUILD_DIR

// Slidev writes into the deck folder's node_modules while it builds, so the
// example deck is built once per run here, never once per browser test file.
export default async function setup(project: TestProject) {
  if (providedBuild) {
    project.provide('overlayExampleBuildDirectory', providedBuild)
    return
  }
  const buildDirectory = await mkdtemp(join(tmpdir(), 'presenter-controls-'))
  await run(
    'pnpm',
    ['exec', 'slidev', 'build', join(repositoryRoot, 'examples/copresenter-deck/slides.md'), '--out', buildDirectory],
    { cwd: join(repositoryRoot, 'frontend') },
  )
  project.provide('overlayExampleBuildDirectory', buildDirectory)
  return () => rm(buildDirectory, { recursive: true, force: true })
}

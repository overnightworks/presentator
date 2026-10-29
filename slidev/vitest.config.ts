import { fileURLToPath } from 'node:url'

import { coverageConfigDefaults, defineConfig } from 'vitest/config'

// SonarCloud reads coverage paths from the repository root, so the run is
// rooted there and the report lands where the CI artifact is picked up.
const repositoryRoot = fileURLToPath(new URL('..', import.meta.url))

export default defineConfig({
  root: repositoryRoot,
  test: {
    // The overlay browser tests each build the example deck from the shared
    // repository folder, and Slidev writes generated files into it.
    fileParallelism: false,
    include: ['slidev/tests/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcovonly'],
      reportsDirectory: 'reports/slidev-coverage',
      exclude: [...coverageConfigDefaults.exclude, '**/package.json', 'slidev/tests/**'],
      thresholds: {
        lines: 100,
        statements: 100,
        branches: 100,
        functions: 100,
      },
    },
  },
})

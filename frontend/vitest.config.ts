import { fileURLToPath } from 'node:url';

import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

import { sveltekitOptions } from './vite.config';

// SonarCloud reads coverage paths from the repository root, and the coverage
// job merges every report into one directory, so the lobby's report is named
// apart from the Slidev addon's lcov.info.
const repositoryRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
	plugins: [sveltekit(sveltekitOptions)],
	test: {
		include: ['src/**/*.test.ts'],
		environment: 'jsdom',
		coverage: {
			provider: 'v8',
			include: ['src/**/*.ts', 'src/**/*.svelte'],
			exclude: ['src/**/*.test.ts', 'src/lib/api/types.ts'],
			reporter: ['text', ['lcovonly', { file: 'lcov-lobby.info', projectRoot: repositoryRoot }]],
			reportsDirectory: '../reports/lobby-coverage'
		}
	}
});

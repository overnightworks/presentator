import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

import { coverageSources, sveltekitOptions } from './vite.config';

export default defineConfig({
	plugins: [sveltekit(sveltekitOptions)],
	// Svelte's runtime resolves to its server build without this, where effects never run.
	resolve: { conditions: ['browser'] },
	test: {
		include: ['src/**/*.test.ts'],
		environment: 'jsdom',
		coverage: {
			// Istanbul counts the same branches in the instrumented build the
			// drive runs, so `pnpm coverage:merge` can add the two reports up.
			provider: 'istanbul',
			...coverageSources,
			reporter: ['text', 'json'],
			reportsDirectory: '../reports/lobby-coverage'
		}
	}
});

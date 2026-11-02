import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

import { coverageSources, sveltekitOptions } from './vite.config';

export default defineConfig({
	plugins: [sveltekit(sveltekitOptions)],
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

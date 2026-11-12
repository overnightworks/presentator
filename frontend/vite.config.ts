import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import istanbul from 'vite-plugin-istanbul';

// Shared with vitest.config.ts so unit tests compile with the build's settings.
// The lobby is a single-page app: FastAPI serves the fallback shell and fills it
// per request.
export const sveltekitOptions: Parameters<typeof sveltekit>[0] = {
	adapter: adapter({
		pages: 'build',
		assets: 'build',
		fallback: 'index.html'
	})
};

// The drive's CI job alone builds the instrumented lobby (`pnpm build:instrumented`);
// the production build and the image never carry counters. The sources and
// exclusions are the ones vitest measures, so both reports name the same files.
// The words modules are written by design-catalogs and held current by
// `design-catalogs --check`, so no test of ours covers them.
export const coverageSources = {
	include: ['src/**/*.ts', 'src/**/*.svelte'],
	exclude: ['src/**/*.test.ts', 'src/lib/api/types.ts', 'src/lib/words/**']
};

export default defineConfig({
	plugins: [
		sveltekit(sveltekitOptions),
		...(process.env.LOBBY_INSTRUMENTED
			? [istanbul({ ...coverageSources, extension: ['.ts', '.svelte'], requireEnv: false, forceBuildInstrument: true })]
			: [])
	]
});

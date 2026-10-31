import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// Shared with vitest.config.ts so unit tests compile with the build's settings.
// The lobby is a single-page app: FastAPI serves the fallback shell and fills it
// per request (docs/decisions/0021-the-lobby-is-a-sveltekit-app.md).
export const sveltekitOptions: Parameters<typeof sveltekit>[0] = {
	adapter: adapter({
		pages: 'build',
		assets: 'build',
		fallback: 'index.html'
	})
};

export default defineConfig({
	plugins: [sveltekit(sveltekitOptions)]
});

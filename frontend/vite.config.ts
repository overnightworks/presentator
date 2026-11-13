import { createHash } from 'node:crypto';

import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, type Plugin } from 'vite';
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

// SvelteKit writes its accessibility announcer with one inline style attribute,
// which the lobby's `style-src 'self'` refuses and `kit.csp` does not hash (it
// hashes only what the server renders). The host admits exactly that attribute
// by its hash, so the build states the hash it contains: the policy never holds
// a copy that a SvelteKit update can leave behind. The name is the host's
// contract (`ANNOUNCER_STYLE_HASH_FILE` in src/presentator/api/lobby_shell.py).
const announcerStyleHashFile = '_app/announcer-style-hash';
const announcerStyle = /<div id="svelte-announcer"[^>]*? style="([^"]*)"/g;

function announcerStyleHash(): Plugin {
	return {
		name: 'lobby-announcer-style-hash',
		apply: 'build',
		generateBundle(_options, bundle) {
			if (this.environment.name !== 'client') return;
			const styles = new Set<string | undefined>();
			for (const output of Object.values(bundle)) {
				if (output.type !== 'chunk') continue;
				for (const match of output.code.matchAll(announcerStyle)) styles.add(match[1]);
			}
			const [style, ...others] = styles;
			if (style === undefined || others.length > 0) {
				this.error(`the client build holds ${styles.size} announcer styles, not the one the host admits`);
			}
			const digest = createHash('sha256').update(style).digest('base64');
			this.emitFile({ type: 'asset', fileName: announcerStyleHashFile, source: `sha256-${digest}\n` });
		}
	};
}

export default defineConfig({
	plugins: [
		sveltekit(sveltekitOptions),
		announcerStyleHash(),
		...(process.env.LOBBY_INSTRUMENTED
			? [istanbul({ ...coverageSources, extension: ['.ts', '.svelte'], requireEnv: false, forceBuildInstrument: true })]
			: [])
	]
});

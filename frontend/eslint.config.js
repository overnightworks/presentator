import eslint from '@eslint/js';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import ts from 'typescript-eslint';

// E4 (#185): SvelteKit's own navigation state is the only thing the lobby
// keeps in the browser, so no app code calls a storage API, and no page
// exports a `snapshot`, which would put form input into sessionStorage.
const NO_STORAGE =
	'The lobby keeps nothing in browser storage (#185 E4): read and write through the API.';
const STORAGE_GLOBALS = ['localStorage', 'sessionStorage', 'indexedDB'].map((name) => ({
	name,
	message: NO_STORAGE
}));
const STORAGE_PROPERTIES = [
	...['window', 'globalThis', 'self'].flatMap((object) =>
		['localStorage', 'sessionStorage', 'indexedDB'].map((property) => ({
			object,
			property,
			message: NO_STORAGE
		}))
	),
	{ object: 'document', property: 'cookie', message: NO_STORAGE }
];
const NO_SNAPSHOT =
	'No page exports a snapshot (#185 E1, E4): SvelteKit would keep its form input in sessionStorage.';
const SNAPSHOT_EXPORTS = [
	"ExportNamedDeclaration > VariableDeclaration > VariableDeclarator[id.name='snapshot']",
	"ExportNamedDeclaration > FunctionDeclaration[id.name='snapshot']",
	"ExportSpecifier[exported.name='snapshot']"
].map((selector) => ({ selector, message: NO_SNAPSHOT }));

export default ts.config(
	{ linterOptions: { reportUnusedDisableDirectives: 'error' } },
	eslint.configs.recommended,
	...ts.configs.strict,
	...svelte.configs.recommended,
	{
		languageOptions: {
			globals: { ...globals.browser, ...globals.node }
		}
	},
	{
		files: ['**/*.svelte', '**/*.svelte.ts'],
		languageOptions: { parserOptions: { parser: ts.parser } }
	},
	{
		// I3 (#185): the CSP allows no style attribute in the app's own markup.
		files: ['src/**/*.svelte'],
		rules: { 'svelte/no-inline-styles': ['error', { allowTransitions: false }] }
	},
	{
		files: ['src/**'],
		rules: {
			'no-restricted-globals': ['error', ...STORAGE_GLOBALS],
			'no-restricted-properties': ['error', ...STORAGE_PROPERTIES],
			'no-restricted-syntax': ['error', ...SNAPSHOT_EXPORTS]
		}
	},
	{
		// The generated wire types are openapi-typescript's output.
		ignores: ['build/', '.svelte-kit/', 'test-results/', 'playwright-report/', 'src/lib/api/types.ts']
	}
);

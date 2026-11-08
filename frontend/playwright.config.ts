import { fileURLToPath } from 'node:url';

import { defineConfig, devices } from '@playwright/test';

// The drive runs against the lobby's build served by FastAPI under the
// lobby's own policy, composed over in-memory stores; each test makes its own
// person at a drive-only route (tests/support/lobby_drive.py), and nobody signs
// in at an issuer. The instance defaults are one store per served lobby, so a
// spec named `*.instance.spec.ts` writes them on a stack of its own per browser;
// the other specs share the first stack and never write them.
const port = 8790;
const instanceSpecs = '**/*.instance.spec.ts';
const repositoryRoot = fileURLToPath(new URL('..', import.meta.url));

const stackAt = (stackPort: number) => ({
	command: `uv run --locked python -m tests.support.lobby_drive serve frontend/build ${stackPort}`,
	cwd: repositoryRoot,
	url: `http://localhost:${stackPort}/health`,
	reuseExistingServer: false,
	timeout: 60_000
});
const serving = (stackPort: number) => ({ baseURL: `http://localhost:${stackPort}` });

export default defineConfig({
	testDir: 'e2e',
	forbidOnly: Boolean(process.env.CI),
	retries: 0,
	reporter: [['list']],
	use: { trace: 'retain-on-failure' },
	projects: [
		{
			name: 'chromium',
			testIgnore: instanceSpecs,
			use: { ...devices['Desktop Chrome'], ...serving(port) }
		},
		{
			name: 'firefox',
			testIgnore: instanceSpecs,
			use: { ...devices['Desktop Firefox'], ...serving(port) }
		},
		{
			name: 'chromium-instance',
			testMatch: instanceSpecs,
			use: { ...devices['Desktop Chrome'], ...serving(port + 1) }
		},
		{
			name: 'firefox-instance',
			testMatch: instanceSpecs,
			use: { ...devices['Desktop Firefox'], ...serving(port + 2) }
		}
	],
	webServer: [stackAt(port), stackAt(port + 1), stackAt(port + 2)]
});

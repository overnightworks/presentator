import { fileURLToPath } from 'node:url';

import { defineConfig, devices } from '@playwright/test';

// The drive runs against the lobby's build served by FastAPI under the
// lobby's own policy, composed over in-memory stores; each test makes its own
// person at a drive-only route (tests/support/lobby_drive.py), and nobody signs
// in at an issuer.
const port = 8790;
const repositoryRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
	testDir: 'e2e',
	forbidOnly: Boolean(process.env.CI),
	retries: 0,
	reporter: [['list']],
	use: {
		baseURL: `http://localhost:${port}`,
		trace: 'retain-on-failure'
	},
	projects: [
		{ name: 'chromium', use: { ...devices['Desktop Chrome'] } },
		{ name: 'firefox', use: { ...devices['Desktop Firefox'] } }
	],
	webServer: {
		command: `uv run --locked python -m tests.support.lobby_drive serve frontend/build ${port}`,
		cwd: repositoryRoot,
		url: `http://localhost:${port}/health`,
		reuseExistingServer: false,
		timeout: 60_000
	}
});

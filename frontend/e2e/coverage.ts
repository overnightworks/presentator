import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { test as base, expect } from './csp.ts';

declare global {
	interface Window {
		__coverage__?: object;
		reportCoverage?: (coverage: string) => Promise<void>;
	}
}

const driveCoverageDirectory = fileURLToPath(
	new URL('../../reports/lobby-coverage/drive', import.meta.url)
);

function leavesTheDocument(error: Error): undefined {
	if (error.message.includes('Execution context was destroyed')) {
		return undefined;
	}
	throw error;
}

// The instrumented build counts in `window.__coverage__`, which each page load
// starts afresh. A document reports its counters as it unloads, and the page
// the test ends on reports them here; `pnpm coverage:merge` adds them to
// vitest's. The production build has no counters and writes nothing.
export const test = base.extend<{ driveCoverage: string[] }>({
	driveCoverage: [
		async ({ context, page }, use, testInfo) => {
			const reports: string[] = [];
			await context.exposeBinding('reportCoverage', (_source, coverage: string) => {
				reports.push(coverage);
			});
			await context.addInitScript(() => {
				addEventListener('pagehide', () => {
					if (window.__coverage__) {
						void window.reportCoverage?.(JSON.stringify(window.__coverage__));
					}
				});
			});
			await use(reports);
			// A page still leaving its document destroys the context this reads; that
			// document reports through its own `pagehide`, so only a navigation is let go.
			await page.waitForLoadState('domcontentloaded').catch(leavesTheDocument);
			const last = await page
				.evaluate(() => JSON.stringify(window.__coverage__ ?? null))
				.catch(leavesTheDocument);
			if (last && last !== 'null') {
				reports.push(last);
			}
			mkdirSync(driveCoverageDirectory, { recursive: true });
			reports.forEach((coverage, index) => {
				writeFileSync(`${driveCoverageDirectory}/${testInfo.testId}-${testInfo.project.name}-${testInfo.retry}-${index}.json`, coverage);
			});
		},
		{ auto: true }
	]
});

export { expect };

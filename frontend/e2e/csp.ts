import { test as base, expect } from '@playwright/test';

declare global {
	interface Window {
		reportViolation?: (violation: string) => Promise<void>;
	}
}

// Every drive fails on a CSP violation: the page's
// securitypolicyviolation event and the console line a browser writes for one,
// so a new inline script is red here, and an announcer style only when the
// build's plugin misreads it.
//
// Firefox fetches /favicon.ico on its own for a page that declares no icon and
// reports a refusal under that page's policy both as the event and as a console
// line, each in its own time. That fetch is the browser's, not the page's
// content, so it is no violation of the page, however often and late it comes.
const browserFaviconFetch = /\bimg-src\b.*\/favicon\.ico\b/;

function record(violations: string[], violation: string): void {
	if (!browserFaviconFetch.test(violation)) {
		violations.push(violation);
	}
}

export const test = base.extend<{ violations: string[] }>({
	violations: [
		async ({ context, page }, use) => {
			const violations: string[] = [];
			await context.exposeBinding('reportViolation', (_source, violation: string) => {
				record(violations, violation);
			});
			await context.addInitScript(() => {
				document.addEventListener(
					'securitypolicyviolation',
					(event) => {
						void window.reportViolation?.(
							`${event.effectiveDirective} ${event.blockedURI} ${event.sourceFile}:${event.lineNumber}`
						);
					},
					true
				);
			});
			page.on('console', (message) => {
				if (/content.security.policy/i.test(message.text())) {
					record(violations, message.text());
				}
			});
			await use(violations);
			expect(violations).toEqual([]);
		},
		{ auto: true }
	]
});

export { expect };

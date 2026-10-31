import { test as base, expect } from '@playwright/test';

declare global {
	interface Window {
		reportViolation?: (violation: string) => Promise<void>;
	}
}

// Every drive fails on a CSP violation (ADR 0021): the page's
// securitypolicyviolation event and the console line a browser writes for one,
// so a changed announcer style or a new inline script is red here.
export const test = base.extend<{ violations: string[] }>({
	violations: [
		async ({ context, page }, use) => {
			const violations: string[] = [];
			await context.exposeBinding('reportViolation', (_source, violation: string) => {
				violations.push(violation);
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
					violations.push(message.text());
				}
			});
			await use(violations);
			expect(violations).toEqual([]);
		},
		{ auto: true }
	]
});

export { expect };

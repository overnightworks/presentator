import { test as base, expect, type ConsoleMessage } from '@playwright/test';

interface PolicyViolation {
	directive: string;
	blockedURI: string;
	sourceFile: string;
	lineNumber: number;
	pageOrigin: string;
}

declare global {
	interface Window {
		reportViolation?: (violation: PolicyViolation) => Promise<void>;
	}
}

// Every drive fails on a CSP violation: the page's
// securitypolicyviolation event and the console line a browser writes for one,
// so a new inline script is red here, and an announcer style only when the
// build's plugin misreads it.
//
// Firefox fetches /favicon.ico on its own for a page that declares no icon and
// reports a refusal under that page's policy both as the event, whose source
// file is its internal "resource", and as a console line from its
// FaviconLoader, each in its own time. That fetch is the browser's, not the
// page's content, so it is no violation of the page, however often and late it
// comes. Any other refusal, a favicon the page itself asks for included, stays
// one.
function firefoxFaviconUrl(pageOrigin: string): string {
	return `${pageOrigin}/favicon.ico`;
}

function isFirefoxFaviconEvent(violation: PolicyViolation): boolean {
	return (
		violation.directive === 'img-src' &&
		violation.blockedURI === firefoxFaviconUrl(violation.pageOrigin) &&
		violation.sourceFile === 'resource'
	);
}

function isFirefoxFaviconLine(message: ConsoleMessage, pageOrigin: string): boolean {
	return (
		message.location().url.endsWith('/FaviconLoader.sys.mjs') &&
		/\bimg-src\b/.test(message.text()) &&
		message.text().includes(` ${firefoxFaviconUrl(pageOrigin)} `)
	);
}

export const test = base.extend<{ violations: string[] }>({
	violations: [
		async ({ baseURL, browserName, context, page }, use) => {
			const firefox = browserName === 'firefox';
			// The console line names no document, and the page may have moved on
			// when it arrives, so the lobby's origin comes from the drive's base.
			if (baseURL === undefined) throw new Error('The CSP fixture needs a baseURL');
			const lobbyOrigin = new URL(baseURL).origin;
			const violations: string[] = [];
			await context.exposeBinding('reportViolation', (_source, violation: PolicyViolation) => {
				if (!(firefox && isFirefoxFaviconEvent(violation))) {
					violations.push(
						`${violation.directive} ${violation.blockedURI} ${violation.sourceFile}:${violation.lineNumber}`
					);
				}
			});
			await context.addInitScript(() => {
				document.addEventListener(
					'securitypolicyviolation',
					(event) => {
						void window.reportViolation?.({
							directive: event.effectiveDirective,
							blockedURI: event.blockedURI,
							sourceFile: event.sourceFile,
							lineNumber: event.lineNumber,
							pageOrigin: location.origin
						});
					},
					true
				);
			});
			page.on('console', (message) => {
				if (
					/content.security.policy/i.test(message.text()) &&
					!(firefox && isFirefoxFaviconLine(message, lobbyOrigin))
				) {
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

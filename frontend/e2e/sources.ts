import { randomUUID } from 'node:crypto';

import type { Locator, Page, Route, TestInfo } from '@playwright/test';

import type { SessionView } from '../src/lib/api/session.ts';
import type {
	ConnectionCheckView,
	CreatedSourceView,
	KeyDraftView,
	SourceView
} from '../src/lib/api/sources.ts';
import type { components } from '../src/lib/api/types.ts';
import { expect } from './person.ts';

export type Words = SessionView['words'];
export type AccessKind = components['schemas']['AccessKind'];

export const NEW_ADDRESS = '/settings/sources/new';
export const SOURCES_LIST = '/settings/sources';
export const SOURCES_API = '/api/sources';
export const CHECK_API = `${SOURCES_API}/new/check`;
export const KIND_API = `${SOURCES_API}/new/kind`;
export const KEY_DRAFT_API = `${SOURCES_API}/new/key-draft`;
export const JSON_ACCEPT = { Accept: 'application/json' };
export const SAME_ORIGIN = { ...JSON_ACCEPT, 'Sec-Fetch-Site': 'same-origin' };
export const ACCESS_TOKEN = 'a-read-only-token-of-this-drive';
const LOCAL_MOUNT_ROUTE = '/drive/local-mount';
const SSH_ADDRESS = 'git@git.example.invalid';

// Chromium and Firefox share the default stack, so every name and address is
// the drive's own: the browser, the moment the file was read and a counter.
const startedAt = Date.now().toString(36);
let counter = 0;
export const aName = (testInfo: TestInfo, label = 'talks'): string =>
	`${label}-${testInfo.project.name}-${startedAt}-${counter++}`;
export const anHttpsAddress = (name: string): string => `https://git.example.invalid/${name}.git`;
// The folder the drive's lobby accepts local sources under is the harness's to say.
export const aLocalAddress = async (page: Page, name: string): Promise<string> => {
	const mount = (await (await page.request.get(LOCAL_MOUNT_ROUTE)).json()) as {
		path: string;
	};
	return `${mount.path}/${name}`;
};
export const anSshAddress = (name: string): string => `${SSH_ADDRESS}:${name}.git`;

// Holds the matching requests on their route until released; `seen` lists their addresses.
// `answer` replaces the continue once released.
export async function holdsRequests(
	page: Page,
	url: string | ((address: URL) => boolean),
	{ method, answer }: { method?: string; answer?: (route: Route) => Promise<void> } = {}
) {
	let release!: () => void;
	const gate = new Promise<void>((resolve) => (release = resolve));
	const seen: string[] = [];
	await page.route(url, async (route) => {
		if (method !== undefined && route.request().method() !== method) {
			return route.continue();
		}
		seen.push(route.request().url());
		await gate;
		return answer ? answer(route) : route.continue();
	});
	return { release, seen };
}

export const secretField = (page: Page, words: Words) =>
	page.getByLabel(words.source_secret, { exact: true });

export async function opensTheForm(page: Page): Promise<void> {
	await page.goto(NEW_ADDRESS);
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
}

// The address is typed and the page has the server's answer for it: only then
// does the form show what the address needs.
export async function typesTheAddress(page: Page, words: Words, url: string): Promise<void> {
	const answered = page.waitForResponse(
		(received) => new URL(received.url()).pathname === KIND_API
	);
	await page.getByLabel(words.source_url, { exact: true }).fill(url);
	await answered;
}

export const folderField = (page: Page, words: Words) =>
	page.getByLabel(words.source_folder_optional, { exact: true });

// A token is typed only when asked for, and only an https address has its field;
// a folder likewise only for a key or web address.
export async function fillsTheForm(
	page: Page,
	words: Words,
	values: { name: string; url: string; secret?: string; folder?: string }
): Promise<void> {
	await page.getByLabel(words.source_name, { exact: true }).fill(values.name);
	await typesTheAddress(page, words, values.url);
	if (values.secret !== undefined) {
		await secretField(page, words).fill(values.secret);
	}
	if (values.folder !== undefined) {
		await folderField(page, words).fill(values.folder);
	}
}

export async function checks(page: Page, words: Words): Promise<ConnectionCheckView> {
	const answered = page.waitForResponse(
		(received) => new URL(received.url()).pathname === CHECK_API
	);
	await page.getByRole('button', { name: words.source_check }).click();
	return (await (await answered).json()) as ConnectionCheckView;
}

async function creates(page: Page, words: Words): Promise<CreatedSourceView> {
	const answered = page.waitForResponse(
		(received) =>
			new URL(received.url()).pathname === SOURCES_API && received.request().method() === 'POST'
	);
	await page.getByRole('button', { name: words.source_create }).click();
	const answer = await answered;
	expect(answer.status(), await answer.text()).toBe(201);
	return (await answer.json()) as CreatedSourceView;
}

// A source that exists: the form filled, checked and created.
export async function createsASource(
	page: Page,
	words: Words,
	values: { name: string; url: string; secret?: string; folder?: string }
): Promise<CreatedSourceView> {
	await fillsTheForm(page, words, values);
	await checks(page, words);
	await expect(page.getByRole('button', { name: words.source_create })).toBeEnabled();
	return creates(page, words);
}

export const keyDraftOf = async (page: Page): Promise<KeyDraftView> =>
	(await (await page.request.post(KEY_DRAFT_API, { headers: SAME_ORIGIN })).json()) as KeyDraftView;

export async function sourceNames(page: Page): Promise<string[]> {
	const answer = await page.request.get(SOURCES_API, { headers: JSON_ACCEPT });
	return ((await answer.json()) as { sources: { name: string }[] }).sources.map(
		(source) => source.name
	);
}

export const tabsOf = (page: Page, words: Words) =>
	page.getByRole('navigation', { name: words.section_settings });

/** The address of a source's own page. */
export const sourceAddress = (name: string): string => `${SOURCES_LIST}/${name}`;

// The address of the SSH source a person of the drive owns; one per worker, so
// two workers on the shared stack never read each other's source.
export const OWN_SOURCE = `git@git.example.invalid:own-${randomUUID().slice(0, 8)}.git`;

/** The newest source at the address: the harness numbers the sources it makes. */
export async function ownSourceName(page: Page, address: string): Promise<string> {
	const answer = await page.request.get(SOURCES_API, { headers: JSON_ACCEPT });
	const { sources } = (await answer.json()) as {
		sources: { name: string; url: string }[];
	};
	const own = sources
		.filter((source) => source.url === address)
		.map((source) => source.name)
		.sort((first, second) => first.length - second.length || first.localeCompare(second));
	expect(own.length, `no source at ${address}`).toBeGreaterThan(0);
	return own[own.length - 1] as string;
}

/** Gives the SSH source a public key through the API, which a fresh one has none of. */
export async function givenADeployKey(page: Page, name: string): Promise<void> {
	const renewed = await page.request.post(`${SOURCES_API}/${name}/access`, {
		headers: SAME_ORIGIN,
		data: { secret: null }
	});
	expect(renewed.status()).toBe(204);
}

/** The shell a browser's page load gets at the address. */
export const shellAt = (page: Page, address: string) =>
	page.request.get(address, { headers: { Accept: 'text/html' } });

/** An HTTPS source made through the Add page, then opened at its own address by a reload. */
export async function opensAnHttpsSource(
	page: Page,
	words: Words,
	testInfo: TestInfo
): Promise<{ name: string; created: CreatedSourceView }> {
	const name = aName(testInfo);
	await opensTheForm(page);
	const created = await createsASource(page, words, {
		name,
		url: anHttpsAddress(name)
	});
	await page.goto(sourceAddress(name));
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
	return { name, created };
}

/** A local-folder source made through the Add page, then opened at its own address. */
export async function opensALocalSource(
	page: Page,
	words: Words,
	testInfo: TestInfo
): Promise<{ name: string; created: CreatedSourceView }> {
	const name = aName(testInfo);
	// The folder the drive's lobby accepts local sources under is the harness's to say.
	const mount = (await (await page.request.get(LOCAL_MOUNT_ROUTE)).json()) as {
		path: string;
	};
	await opensTheForm(page);
	await fillsTheForm(page, words, { name, url: `${mount.path}/${name}` });
	await checks(page, words);
	await expect(page.getByRole('button', { name: words.source_create })).toBeEnabled();
	const created = await creates(page, words);
	await page.goto(sourceAddress(name));
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
	return { name, created };
}

// The page answer of one source, changed on its way to the page.
export async function answersWith(
	page: Page,
	name: string,
	change: (view: SourceView) => SourceView
): Promise<void> {
	await page.route(`**${SOURCES_API}/${name}`, async (route: Route) => {
		if (route.request().method() !== 'GET') {
			return route.continue();
		}
		const real = await route.fetch();
		return route.fulfill({
			response: real,
			json: change((await real.json()) as SourceView)
		});
	});
}

export const removeButton = (page: Page, words: Words): Locator =>
	page.getByRole('button', { name: words.source_remove_button });
export const dialogOf = (page: Page): Locator => page.getByRole('dialog');
export const cancelOf = (page: Page, words: Words): Locator =>
	dialogOf(page).getByRole('button', { name: words.source_remove_cancel });
export const confirmOf = (page: Page, words: Words): Locator =>
	dialogOf(page).getByRole('button', { name: words.source_remove_confirm, exact: true });

export async function opensTheDialog(page: Page, words: Words): Promise<void> {
	await removeButton(page, words).click();
	await expect(dialogOf(page)).toBeVisible();
}

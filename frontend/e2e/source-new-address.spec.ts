import { expect, test } from './person.ts';
import { expectsNoHorizontalScroll, sessionOf } from './scenario.ts';
import {
	ACCESS_TOKEN,
	aLocalAddress,
	aName,
	anHttpsAddress,
	anSshAddress,
	CHECK_API,
	checks,
	folderField,
	KIND_API,
	opensTheForm,
	SOURCES_API,
	secretField,
	typesTheAddress
} from './sources.ts';

test.describe('the address decides what the Add source form shows', () => {
	test.use({ person: { role: 'admin' } });

	for (const [shape, token, key] of [
		['https', true, false],
		['ssh', false, true],
		['folder', false, false]
	] as const) {
		test(`a ${shape} address shows the token field ${token}, the key ${key} and the folder ${token || key}`, async ({
			page
		}, testInfo) => {
			const { words } = await sessionOf(page);
			const name = aName(testInfo);
			const url = {
				https: () => anHttpsAddress(name),
				ssh: () => anSshAddress(name),
				folder: () => aLocalAddress(page, name)
			}[shape]();
			await opensTheForm(page);

			await typesTheAddress(page, words, await url);

			await expect(secretField(page, words)).toHaveCount(token ? 1 : 0);
			await expect(page.getByText(words.source_deploy_key_heading)).toHaveCount(key ? 1 : 0);
			await expect(folderField(page, words)).toHaveCount(token || key ? 1 : 0);
			await expect(page.getByRole('button', { name: words.source_check })).toBeEnabled();
		});
	}

	for (const url of [
		'http://git.example.invalid/talks.git',
		'ftp://git.example.invalid/talks.git'
	]) {
		test(`${url.split(':')[0]} names no access: no field, and Check answers the unknown-access sentence`, async ({
			page
		}) => {
			const { words } = await sessionOf(page);
			await opensTheForm(page);

			await typesTheAddress(page, words, url);
			await expect(secretField(page, words)).toHaveCount(0);
			await expect(page.getByText(words.source_deploy_key_heading)).toHaveCount(0);
			await page.getByLabel(words.source_name, { exact: true }).fill('talks');
			await checks(page, words);

			await expect(page.getByRole('alert')).toHaveText(words.source_refused_unknown_access);
		});
	}

	test('a token typed for https is gone when a key address follows, and the check carries no secret', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await typesTheAddress(page, words, anHttpsAddress(name));
		await secretField(page, words).fill(ACCESS_TOKEN);

		await typesTheAddress(page, words, anSshAddress(name));
		await expect(secretField(page, words)).toHaveCount(0);
		const sent = page.waitForRequest((request) => new URL(request.url()).pathname === CHECK_API);
		await page.getByRole('button', { name: words.source_check }).click();

		expect((await sent).postDataJSON()).toMatchObject({ secret: null });
		expect((await sent).postData()).not.toContain(ACCESS_TOKEN);
	});

	test('the empty form shows no folder field', async ({ page }) => {
		const { words } = await sessionOf(page);

		await opensTheForm(page);

		await expect(folderField(page, words)).toHaveCount(0);
	});

	test('a folder typed for https is gone when a server folder follows, and Create sends none', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await page.getByLabel(words.source_name, { exact: true }).fill(name);
		await typesTheAddress(page, words, anHttpsAddress(name));
		await folderField(page, words).fill('examples');

		await typesTheAddress(page, words, await aLocalAddress(page, name));
		await expect(folderField(page, words)).toHaveCount(0);
		await checks(page, words);
		const sent = page.waitForRequest(
			(request) => new URL(request.url()).pathname === SOURCES_API && request.method() === 'POST'
		);
		await page.getByRole('button', { name: words.source_create }).click();

		expect((await sent).postDataJSON()).toMatchObject({ folder: '' });
	});

	test('Create sends the folder typed for a key address and Check does not', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await page.getByLabel(words.source_name, { exact: true }).fill(name);
		await typesTheAddress(page, words, anSshAddress(name));
		await folderField(page, words).fill('examples');
		const checked = page.waitForRequest((request) => new URL(request.url()).pathname === CHECK_API);
		await checks(page, words);
		const created = page.waitForRequest(
			(request) => new URL(request.url()).pathname === SOURCES_API && request.method() === 'POST'
		);
		await page.getByRole('button', { name: words.source_create }).click();

		expect((await checked).postDataJSON()).not.toHaveProperty('folder');
		expect((await created).postDataJSON()).toMatchObject({ folder: 'examples' });
	});

	test('the token field comes back empty when https follows a key address', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await typesTheAddress(page, words, anHttpsAddress(name));
		await secretField(page, words).fill(ACCESS_TOKEN);
		await typesTheAddress(page, words, anSshAddress(name));
		await expect(page.getByText(words.source_deploy_key_heading)).toBeVisible();

		await typesTheAddress(page, words, anHttpsAddress(name));

		await expect(page.getByText(words.source_deploy_key_heading)).toHaveCount(0);
		await expect(secretField(page, words)).toHaveValue('');
	});

	test('Check stays disabled on a blank address and until the address has its answer', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const check = page.getByRole('button', { name: words.source_check });
		let release: () => void = () => undefined;
		const held = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.route(`**${KIND_API}`, async (route) => {
			await held;
			await route.continue();
		});
		await opensTheForm(page);
		await expect(check).toBeDisabled();

		await page.getByLabel(words.source_url, { exact: true }).fill('   ');
		await expect(check).toBeDisabled();
		await page.getByLabel(words.source_url, { exact: true }).fill(anHttpsAddress(aName(testInfo)));

		await expect(page.getByRole('status')).toHaveText(words.source_kind_pending);
		await expect(check).toBeDisabled();
		release();
		await expect(check).toBeEnabled();
		await expect(page.getByRole('status')).toHaveCount(0);
	});

	test('a failed lookup says so with a retry and names no access', async ({ page }, testInfo) => {
		const { words } = await sessionOf(page);
		let failing = true;
		await page.route(`**${KIND_API}`, (route) =>
			failing ? route.fulfill({ status: 500, json: {} }) : route.continue()
		);
		await opensTheForm(page);
		await page.getByLabel(words.source_url, { exact: true }).fill(anHttpsAddress(aName(testInfo)));

		await expect(page.getByRole('alert')).toContainText(words.source_kind_failed);
		await expect(secretField(page, words)).toHaveCount(0);
		await expect(page.getByRole('button', { name: words.source_check })).toBeDisabled();
		failing = false;
		await page.getByRole('button', { name: words.source_kind_retry }).click();

		await expect(secretField(page, words)).toBeVisible();
		await expect(page.getByRole('alert')).toHaveCount(0);
	});

	test('every field shown at 390 px leaves no horizontal scroll', async ({ page }, testInfo) => {
		const { words } = await sessionOf(page);
		await page.setViewportSize({ width: 390, height: 900 });
		await opensTheForm(page);

		await typesTheAddress(page, words, anHttpsAddress(aName(testInfo)));
		await expect(folderField(page, words)).toBeVisible();
		await expect(secretField(page, words)).toBeVisible();
		await expectsNoHorizontalScroll(page);

		await typesTheAddress(page, words, anSshAddress(aName(testInfo)));
		await expect(page.getByText(words.source_deploy_key_heading)).toBeVisible();
		await expectsNoHorizontalScroll(page);
	});
});

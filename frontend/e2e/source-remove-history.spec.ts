import type { WebhookSecretView } from '../src/lib/api/sources.ts';
import { expect, test } from './person.ts';
import { sessionOf } from './scenario.ts';
import {
	cancelOf,
	dialogOf,
	opensAnHttpsSource,
	opensTheDialog,
	removeButton,
	SOURCES_LIST,
	sourceAddress
} from './sources.ts';

test.describe('as an admin', () => {
	test.use({ person: { role: 'admin' } });

	// Cell 28.
	test('Back to another source rebuilds its page: no secret, token, open dialog or counts of the first', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const second = await opensAnHttpsSource(page, words, testInfo);
		const first = await opensAnHttpsSource(page, words, testInfo);
		await page.goto(sourceAddress(second.name));
		await page.getByRole('link', { name: words.sources_back }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await page.getByRole('link', { name: first.name, exact: true }).click();
		await expect(page).toHaveURL(sourceAddress(first.name));
		const secret = (await (
			await Promise.all([
				page.waitForResponse((received) => received.url().endsWith('/webhook-secret')),
				page.getByRole('button', { name: words.source_renew, exact: true }).last().click()
			]).then(([answer]) => answer.json())
		) as WebhookSecretView).webhook_secret;
		await page.getByLabel(words.source_secret, { exact: true }).fill('typed-but-not-sent');
		await expect(page.locator('body')).toContainText(secret);
		await opensTheDialog(page, words);
		await cancelOf(page, words).focus();

		await page.evaluate(() => history.go(-2));

		await expect(page).toHaveURL(sourceAddress(second.name));
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(second.name);
		await expect(page.locator('body')).not.toContainText(secret);
		await expect(page.getByLabel(words.source_secret, { exact: true })).toHaveValue('');
		await expect(dialogOf(page)).toBeHidden();
		const asked = page.waitForResponse((received) => received.url().endsWith('/removal'));
		await removeButton(page, words).click();
		expect((await asked).url()).toContain(`/${second.name}/removal`);
		await expect(dialogOf(page).getByRole('heading', { level: 2 })).toContainText(second.name);
	});

	// Cell 28b.
	test('Back to the same source in an earlier entry rebuilds its page too', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await page.getByRole('link', { name: words.sources_back }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await page.getByRole('link', { name, exact: true }).click();
		await expect(page).toHaveURL(sourceAddress(name));
		const [answer] = await Promise.all([
			page.waitForResponse((received) => received.url().endsWith('/webhook-secret')),
			page.getByRole('button', { name: words.source_renew, exact: true }).last().click()
		]);
		const { webhook_secret: secret } = (await answer.json()) as WebhookSecretView;
		await page.getByLabel(words.source_secret, { exact: true }).fill('typed-but-not-sent');
		await expect(page.locator('body')).toContainText(secret);
		await opensTheDialog(page, words);

		await page.evaluate(() => history.go(-2));

		await expect(page).toHaveURL(sourceAddress(name));
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
		await expect(page.locator('body')).not.toContainText(secret);
		await expect(page.getByLabel(words.source_secret, { exact: true })).toHaveValue('');
		await expect(dialogOf(page)).toBeHidden();
	});
});

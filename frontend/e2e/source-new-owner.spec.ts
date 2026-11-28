import { expect, test } from './person.ts';
import { expectsNoHorizontalScroll, wordsOf } from './scenario.ts';
import {
	aName,
	anHttpsAddress,
	checks,
	fillsTheForm,
	opensTheForm,
	ownerField,
	sourceNames
} from './sources.ts';

test.describe('the owner of a new source', () => {
	test.use({ person: { role: 'admin' } });

	for (const width of [390, 1280]) {
		test(`the form offers an optional owner field with its hint at ${width} px`, async ({
			page
		}) => {
			const words = await wordsOf(page);
			await page.setViewportSize({ width, height: 900 });

			await opensTheForm(page);

			await expect(ownerField(page, words)).toHaveValue('');
			await expect(ownerField(page, words)).toHaveAccessibleDescription(words['sourceNew.ownerHint']);
			await expectsNoHorizontalScroll(page);
		});
	}

	test('a username no member holds is refused, and the corrected form creates on the same Check', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await fillsTheForm(page, words, { name, url: anHttpsAddress(name) });
		await ownerField(page, words).fill('nobody-holds-this-username');
		await checks(page, words);

		await page.getByRole('button', { name: words['sourceNew.create'] }).click();

		await expect(page.getByRole('alert')).toHaveText(words['source.refusedOwner']);
		await expect(ownerField(page, words)).toHaveValue('nobody-holds-this-username');
		expect(await sourceNames(page)).not.toContain(name);

		await ownerField(page, words).fill('');
		await page.getByRole('button', { name: words['sourceNew.create'] }).click();

		await expect(page.locator('body')).toContainText(words['sourceNew.createdToast']);
		expect(await sourceNames(page)).toContain(name);
	});
});

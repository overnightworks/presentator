import { describe, expect, it } from 'vitest';

import { ariaCurrent } from '#lib/section.ts';

describe('the header link marked as the current page', () => {
	it.each([
		{ address: '/', decks: 'page', settings: undefined },
		{ address: '/settings', decks: undefined, settings: 'page' },
		{ address: '/settings/sources', decks: undefined, settings: 'page' },
		{ address: '/settingsish', decks: undefined, settings: undefined },
		{ address: '/deck/anything', decks: 'page', settings: undefined },
		{ address: '/deck/anything/', decks: 'page', settings: undefined },
		{ address: '/deck/anything/pdf', decks: undefined, settings: undefined },
		{ address: '/deck/a/b/c', decks: undefined, settings: undefined },
		{ address: '/deck/', decks: undefined, settings: undefined },
		{ address: '/decks', decks: undefined, settings: undefined },
		{ address: '/account', decks: undefined, settings: undefined },
		{ address: '/nope', decks: undefined, settings: undefined }
	])('marks $decks for Decks and $settings for Settings at $address', (row) => {
		expect(ariaCurrent(row.address, 'decks')).toBe(row.decks);
		expect(ariaCurrent(row.address, 'settings')).toBe(row.settings);
	});
});

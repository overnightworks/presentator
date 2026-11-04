import { describe, expect, it } from 'vitest';

import { ariaCurrent, tabCurrent } from '#lib/section.ts';

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

describe('the Settings tab marked as the current page', () => {
	it.each([
		{ address: '/settings', general: 'page', sources: undefined },
		{ address: '/settings/sources', general: undefined, sources: 'page' },
		{ address: '/settings/sources/new', general: undefined, sources: 'page' },
		{ address: '/settings/sourcesish', general: 'page', sources: undefined }
	])('marks $general for General and $sources for Sources at $address', (row) => {
		expect(tabCurrent(row.address, 'general')).toBe(row.general);
		expect(tabCurrent(row.address, 'sources')).toBe(row.sources);
	});
});

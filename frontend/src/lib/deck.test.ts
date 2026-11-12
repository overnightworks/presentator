import { describe, expect, it } from 'vitest';

import type { FailureView } from '#lib/api/decks.ts';
import { buildsWith, failureWords, stateWords } from '#lib/deck.ts';
import { words } from '#lib/words/en.ts';

const failure = (overrides: Partial<FailureView>): FailureView => ({
	reason: 'toolchain',
	text: null,
	written_megabytes: null,
	allowed_megabytes: null,
	allowed_entries: null,
	...overrides
});

describe('stateWords', () => {
	it('names each state by its word', () => {
		expect(stateWords(words)).toEqual({
			ready: words['deck.stateReady'],
			building: words['deck.stateBuilding'],
			failed: words['deck.stateFailed'],
			'never-built': words['deck.stateNeverBuilt']
		});
	});
});

describe('buildsWith', () => {
	it('lists the theme names the way the language lists things', () => {
		expect(buildsWith(['alpha', 'beta', 'gamma'], words, 'de')).toBe(
			words['deck.buildsWith']({ themes: 'alpha, beta und gamma' })
		);
	});
});

describe('failureWords', () => {
	it.each([
		{ reason: 'toolchain', given: { text: 'the toolchain spoke' }, worded: 'the toolchain spoke' },
		{ reason: 'no-words', given: {}, worded: null },
		{
			reason: 'output-megabytes',
			given: { written_megabytes: 700, allowed_megabytes: 500 },
			worded: words['deck.failedOutputMegabytes']({ allowed: '500', written: '700' })
		},
		{
			reason: 'output-entries',
			given: { allowed_entries: 20000 },
			worded: words['deck.failedOutputEntries']({ entries: '20000' })
		},
		{ reason: 'output-shape', given: {}, worded: words['deck.failedOutputShape'] },
		{
			reason: 'deck-megabytes',
			given: { allowed_megabytes: 300, allowed_entries: 9000 },
			worded: words['deck.failedDeckMegabytes']({ entries: '9000', megabytes: '300' })
		},
		{ reason: 'deck-paths', given: {}, worded: words['deck.failedDeckPaths'] }
	] as const)('words the $reason reason', ({ reason, given, worded }) => {
		expect(failureWords(failure({ reason, ...given }), words)).toBe(worded);
	});

	it('words a build that left no failure as no words', () => {
		expect(failureWords(null, words)).toBeNull();
	});
});

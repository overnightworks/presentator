import { describe, expect, it } from 'vitest';

import type { FailureView } from '#lib/api/decks.ts';
import type { LobbyText } from '#lib/api/session.ts';
import { buildsWith, failureWords, stateWords } from '#lib/deck.ts';

// A scenario's own templates: none of them is the catalog's sentence.
const words = {
	deck_state_ready: 'steady',
	deck_state_building: 'cooking',
	deck_state_failed: 'broken',
	deck_state_never_built: 'untouched',
	deck_builds_with: 'Themes in use: {themes}',
	deck_theme_separator: ' + ',
	deck_failed_output_megabytes: 'wrote {written} of at most {allowed} MB',
	deck_failed_output_entries: 'more than {entries} files',
	deck_failed_output_shape: 'a file that cannot be taken',
	deck_failed_deck_megabytes: 'over {megabytes} MB or {entries} files',
	deck_failed_deck_paths: 'a path that cannot be built'
} as LobbyText;

const failure = (overrides: Partial<FailureView>): FailureView => ({
	reason: 'toolchain',
	text: null,
	written_megabytes: null,
	allowed_megabytes: null,
	allowed_entries: null,
	...overrides
});

describe('stateWords', () => {
	it('names each state by the words given', () => {
		expect(stateWords(words)).toEqual({
			ready: 'steady',
			building: 'cooking',
			failed: 'broken',
			'never-built': 'untouched'
		});
	});
});

describe('buildsWith', () => {
	it('joins the theme names by the separator given', () => {
		expect(buildsWith(['alpha', 'beta', 'gamma'], words)).toBe('Themes in use: alpha + beta + gamma');
	});
});

describe('failureWords', () => {
	it.each([
		{ reason: 'toolchain', given: { text: 'the toolchain spoke' }, worded: 'the toolchain spoke' },
		{ reason: 'no-words', given: {}, worded: null },
		{
			reason: 'output-megabytes',
			given: { written_megabytes: 700, allowed_megabytes: 500 },
			worded: 'wrote 700 of at most 500 MB'
		},
		{ reason: 'output-entries', given: { allowed_entries: 20000 }, worded: 'more than 20000 files' },
		{ reason: 'output-shape', given: {}, worded: 'a file that cannot be taken' },
		{
			reason: 'deck-megabytes',
			given: { allowed_megabytes: 300, allowed_entries: 9000 },
			worded: 'over 300 MB or 9000 files'
		},
		{ reason: 'deck-paths', given: {}, worded: 'a path that cannot be built' }
	] as const)('words the $reason reason', ({ reason, given, worded }) => {
		expect(failureWords(failure({ reason, ...given }), words)).toBe(worded);
	});

	it('words a build that left no failure as no words', () => {
		expect(failureWords(null, words)).toBeNull();
	});
});

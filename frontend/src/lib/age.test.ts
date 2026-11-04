import { describe, expect, it } from 'vitest';

import { ageInWords } from '#lib/age.ts';

const NOW = Date.parse('2026-11-03T12:00:00Z');
const minutesAgo = (minutes: number): string => new Date(NOW - minutes * 60_000).toISOString();

describe('ageInWords', () => {
	it.each([
		{ minutes: 0, tag: 'en-US', worded: 'now' },
		{ minutes: 2, tag: 'en-US', worded: '2 minutes ago' },
		{ minutes: 180, tag: 'en-US', worded: '3 hours ago' },
		{ minutes: 24 * 60, tag: 'en-US', worded: 'yesterday' },
		{ minutes: 6 * 24 * 60, tag: 'en-US', worded: '6 days ago' },
		{ minutes: 2, tag: 'de', worded: 'vor 2 Minuten' }
	])('words $minutes minutes in $tag', ({ minutes, tag, worded }) => {
		expect(ageInWords(minutesAgo(minutes), NOW, tag)).toBe(worded);
	});

	it('never words an instant ahead of the clock as the future', () => {
		expect(ageInWords(minutesAgo(-5), NOW, 'en-US')).toBe('now');
	});
});

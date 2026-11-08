import { describe, expect, it } from 'vitest';

import { ageInWords, durationInWords } from '#lib/age.ts';

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

describe('durationInWords', () => {
	const secondsAgo = (seconds: number): string => new Date(NOW - seconds * 1000).toISOString();

	it.each([
		{ seconds: 40, tag: 'en-US', worded: '40 seconds' },
		{ seconds: 1, tag: 'en-US', worded: '1 second' },
		{ seconds: 150, tag: 'en-US', worded: '2 minutes' },
		{ seconds: 3 * 3600 + 59, tag: 'en-US', worded: '3 hours' },
		{ seconds: 2 * 86400 + 5, tag: 'en-US', worded: '2 days' },
		{ seconds: 40, tag: 'de', worded: '40 Sekunden' }
	])('words $seconds seconds in $tag', ({ seconds, tag, worded }) => {
		expect(durationInWords(secondsAgo(seconds), NOW, tag)).toBe(worded);
	});

	it('never words an instant ahead of the clock as a negative length', () => {
		expect(durationInWords(secondsAgo(-30), NOW, 'en-US')).toBe('0 seconds');
	});
});

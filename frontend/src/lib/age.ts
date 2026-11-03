const SECOND = 1000;
const UNITS: readonly { unit: Intl.RelativeTimeFormatUnit; milliseconds: number }[] = [
	{ unit: 'day', milliseconds: 24 * 60 * 60 * SECOND },
	{ unit: 'hour', milliseconds: 60 * 60 * SECOND },
	{ unit: 'minute', milliseconds: 60 * SECOND }
];

/** How long ago, worded by the browser in the person's language. */
export function ageInWords(changedAt: string, now: number, languageTag: string): string {
	const elapsed = Math.max(0, now - Date.parse(changedAt));
	const words = new Intl.RelativeTimeFormat(languageTag, { numeric: 'auto' });
	for (const { unit, milliseconds } of UNITS) {
		if (elapsed >= milliseconds) {
			return words.format(-Math.floor(elapsed / milliseconds), unit);
		}
	}
	return words.format(0, 'second');
}

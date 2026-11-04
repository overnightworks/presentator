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

/** How long something has run since `startedAt`, in its largest whole unit, worded by the browser. */
export function durationInWords(startedAt: string, now: number, languageTag: string): string {
	const elapsed = Math.max(0, now - Date.parse(startedAt));
	const { unit, milliseconds } = UNITS.find((candidate) => elapsed >= candidate.milliseconds) ?? {
		unit: 'second',
		milliseconds: SECOND
	};
	return new Intl.NumberFormat(languageTag, { style: 'unit', unit, unitDisplay: 'long' }).format(
		Math.floor(elapsed / milliseconds)
	);
}

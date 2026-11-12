import type { DeckState, FailureView } from '#lib/api/decks.ts';
import type { Words } from '#lib/words/de.ts';

/** The catalog's word for each state a deck is in. */
export function stateWords(words: Words): Record<DeckState, string> {
	return {
		ready: words['deck.stateReady'],
		building: words['deck.stateBuilding'],
		failed: words['deck.stateFailed'],
		'never-built': words['deck.stateNeverBuilt']
	};
}

/** The words a failed build is shown in: the toolchain's own, or the catalog's with the counts; none when it left no message. */
export function failureWords(failure: FailureView | null, words: Words): string | null {
	if (failure === null) {
		return null;
	}
	switch (failure.reason) {
		case 'toolchain':
			return failure.text;
		case 'no-words':
			return null;
		case 'output-megabytes':
			return words['deck.failedOutputMegabytes']({
				allowed: String(failure.allowed_megabytes),
				written: String(failure.written_megabytes)
			});
		case 'output-entries':
			return words['deck.failedOutputEntries']({ entries: String(failure.allowed_entries) });
		case 'output-shape':
			return words['deck.failedOutputShape'];
		case 'deck-megabytes':
			return words['deck.failedDeckMegabytes']({
				entries: String(failure.allowed_entries),
				megabytes: String(failure.allowed_megabytes)
			});
		case 'deck-paths':
			return words['deck.failedDeckPaths'];
	}
}

/** What this instance's toolchain carries, listed the way the language lists things. */
export function buildsWith(themes: readonly string[], words: Words, languageTag: string): string {
	return words['deck.buildsWith']({ themes: new Intl.ListFormat(languageTag).format(themes) });
}

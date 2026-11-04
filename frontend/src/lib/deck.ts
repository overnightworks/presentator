import type { DeckState, FailureView } from '#lib/api/decks.ts';
import type { LobbyText } from '#lib/api/session.ts';

/** The catalog's word for each state a deck is in. */
export function stateWords(words: LobbyText): Record<DeckState, string> {
	return {
		ready: words.deck_state_ready,
		building: words.deck_state_building,
		failed: words.deck_state_failed,
		'never-built': words.deck_state_never_built
	};
}

/** Puts each `{name}` of a catalog sentence in place; the wire carries every count a sentence names. */
export function fill(template: string, values: Readonly<Record<string, string | number | null>>): string {
	return template.replace(/\{(\w+)\}/g, (_placeholder, name: string) => String(values[name]));
}

/** The words a failed build is shown in: the toolchain's own, or the catalog's with the counts; none when it left no message. */
export function failureWords(failure: FailureView | null, words: LobbyText): string | null {
	if (failure === null) {
		return null;
	}
	const counts = {
		written: failure.written_megabytes,
		allowed: failure.allowed_megabytes,
		megabytes: failure.allowed_megabytes,
		entries: failure.allowed_entries
	};
	switch (failure.reason) {
		case 'toolchain':
			return failure.text;
		case 'no-words':
			return null;
		case 'output-megabytes':
			return fill(words.deck_failed_output_megabytes, counts);
		case 'output-entries':
			return fill(words.deck_failed_output_entries, counts);
		case 'output-shape':
			return fill(words.deck_failed_output_shape, counts);
		case 'deck-megabytes':
			return fill(words.deck_failed_deck_megabytes, counts);
		case 'deck-paths':
			return fill(words.deck_failed_deck_paths, counts);
	}
}

/** What this instance's toolchain carries, named by the catalog's separator. */
export function buildsWith(themes: readonly string[], words: LobbyText): string {
	return fill(words.deck_builds_with, { themes: themes.join(words.deck_theme_separator) });
}

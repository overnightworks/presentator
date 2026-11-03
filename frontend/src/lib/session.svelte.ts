import type { ThemeChoice } from '#lib/api/account.ts';
import type { SessionView } from '#lib/api/session.ts';

// Nothing here reaches browser storage: the session is the server's, read on
// each load, and this holds only what the committed page data last said.
let view = $state<SessionView | undefined>();

/** Takes the session a load has committed to the page. */
export function commitSession(committed: SessionView): void {
	view = committed;
}

/** The theme as the person just chose it, before or without a reload. */
export function showTheme(theme: ThemeChoice): void {
	if (view !== undefined) {
		view = { ...view, theme };
	}
}

export function currentTheme(): ThemeChoice | undefined {
	return view?.theme;
}

// The page's look and language follow the store, so a change needs no reload.
$effect.root(() => {
	$effect(() => {
		if (view === undefined) {
			return;
		}
		const root = document.documentElement;
		if (view.theme === 'follow-system') {
			delete root.dataset.theme;
		} else {
			root.dataset.theme = view.theme;
		}
		root.lang = view.words.language_tag;
	});
});

/** The language tag the person's words are in; ages are worded in it. */
export function languageTag(): string | undefined {
	return view?.words.language_tag;
}

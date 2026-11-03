import type { ThemeChoice } from '#lib/api/account.ts';
import type { SessionView } from '#lib/api/session.ts';

// Nothing here reaches browser storage: the session is the server's, read on
// each load, and this holds only what the committed page data last said.
// Every reader sits under the root layout, which commits the session before
// its children read it; only the root effect below can run earlier.
let view = $state<SessionView | undefined>() as SessionView;

/** Takes the session a load has committed to the page. */
export function commitSession(committed: SessionView): void {
	view = committed;
}

/** The theme as the person just chose it, before or without a reload. */
export function showTheme(theme: ThemeChoice): void {
	view = { ...view, theme };
}

export function currentTheme(): ThemeChoice {
	return view.theme;
}

// The page's look and language follow the store, so a change needs no reload.
$effect.root(() => {
	$effect(() => {
		if (view === undefined) {
			return;
		}
		// The same marks the server's shell writes on first paint.
		document.documentElement.dataset.theme = view.theme;
		document.documentElement.lang = view.words.language_tag;
	});
});

/** The language tag the person's words are in; ages are worded in it. */
export function languageTag(): string {
	return view.words.language_tag;
}

type Section = 'decks' | 'settings';

const SETTINGS_ADDRESS = '/settings';
// The deck page itself: one slug, never an address below it.
const DECK_PAGE = /^\/deck\/[^/]+\/?$/;

/** The header section an address belongs to, or none for the other addresses. */
function sectionOf(pathname: string): Section | null {
	if (pathname === '/' || DECK_PAGE.test(pathname)) {
		return 'decks';
	}
	if (pathname === SETTINGS_ADDRESS || pathname.startsWith(`${SETTINGS_ADDRESS}/`)) {
		return 'settings';
	}
	return null;
}

/** The `aria-current` value of a header link to `section` at `pathname`. */
export function ariaCurrent(pathname: string, section: Section): 'page' | undefined {
	return sectionOf(pathname) === section ? 'page' : undefined;
}

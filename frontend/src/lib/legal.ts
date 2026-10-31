import type { LegalLink } from 'overnightworks-appkit';
import type { LobbyText } from '#lib/api/session.ts';

// The operator's legal pages, which every presentator page links to; the
// Datenschutz page has a presentator section.
const IMPRESSUM = 'https://hallucinai.de/impressum';
const DATENSCHUTZ = 'https://hallucinai.de/datenschutz#presentator';

export function legalLinks(words: LobbyText): readonly LegalLink[] {
	return [
		{ label: words.footer_impressum, href: IMPRESSUM },
		{ label: words.footer_datenschutz, href: DATENSCHUTZ }
	];
}

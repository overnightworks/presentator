import type { LegalLink } from 'overnightworks-design';
import type { LobbyText } from '#lib/api/session.ts';

// The operator's legal pages, which the person menu and the sign-in pages link to; the
// Datenschutz and Nutzungsbedingungen pages have a presentator section.
const IMPRESSUM = 'https://hallucinai.de/impressum';
const DATENSCHUTZ = 'https://hallucinai.de/datenschutz#presentator';
const NUTZUNGSBEDINGUNGEN = 'https://hallucinai.de/nutzungsbedingungen#presentator';

export function legalLinks(words: LobbyText): readonly LegalLink[] {
	return [
		{ label: words.legal_impressum, href: IMPRESSUM },
		{ label: words.legal_datenschutz, href: DATENSCHUTZ },
		{ label: words.legal_nutzungsbedingungen, href: NUTZUNGSBEDINGUNGEN }
	];
}

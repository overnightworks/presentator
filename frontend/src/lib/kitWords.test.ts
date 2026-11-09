import { describe, expect, it } from 'vitest';

import type { LobbyText } from '#lib/api/session.ts';
import { accountMenuWords, signInPageWords } from '#lib/kitWords.ts';

const words = {
	log_out: 'out',
	theme_title: 'theme',
	theme_follow_system: 'follow',
	theme_light: 'light',
	theme_dark: 'dark',
	sign_in_logged_out: 'gone',
	sign_in_log_in: 'in',
	sign_in_failed: 'failed',
	sign_in_failed_banner: 'banner',
	sign_in_try_again: 'again',
	sign_in_no_access: 'denied',
	sign_in_no_access_sentence: 'no way into {app}.',
	sign_in_signing_in: 'busy',
	sign_in_in_other_window: 'elsewhere',
	sign_in_open_in_tab: 'tab it',
	sign_in_unfinished: 'not done'
} as LobbyText;

describe('accountMenuWords', () => {
	it('gives the kit the menu words of the catalog', () => {
		expect(accountMenuWords(words)).toEqual({
			theme: 'theme',
			followSystem: 'follow',
			light: 'light',
			dark: 'dark',
			logOut: 'out'
		});
	});
});

describe('signInPageWords', () => {
	it('gives the kit the sign-in words of the catalog', () => {
		expect(signInPageWords(words)).toMatchObject({
			loggedOut: 'gone',
			logIn: 'in',
			loginFailed: 'failed',
			loginFailedBanner: 'banner',
			tryAgain: 'again',
			noAccess: 'denied',
			signingIn: 'busy',
			inOtherWindow: 'elsewhere',
			openInTab: 'tab it',
			signInUnfinished: 'not done',
			logOut: 'out'
		});
	});

	it('fills the app name into the no-access sentence', () => {
		expect(signInPageWords(words).noAccessSentence('Hafen')).toBe('no way into Hafen.');
	});
});

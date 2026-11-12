import { describe, expect, it } from 'vitest';

import { accountMenuWords, signInPageWords } from '#lib/kitWords.ts';
import { words } from '#lib/words/en.ts';

describe('accountMenuWords', () => {
	it('gives the kit the menu words of the catalog', () => {
		expect(accountMenuWords(words)).toEqual({
			theme: words['account.theme'],
			followSystem: words['account.followSystem'],
			light: words['account.light'],
			dark: words['account.dark'],
			logOut: words['logout.button']
		});
	});
});

describe('signInPageWords', () => {
	it('gives the kit the sign-in words of the catalog', () => {
		expect(signInPageWords(words)).toMatchObject({
			loggedOut: words['signIn.loggedOut'],
			logIn: words['signIn.logIn'],
			loginFailed: words['signIn.loginFailed'],
			loginFailedBanner: words['signIn.loginFailedBanner'],
			tryAgain: words['signIn.tryAgain'],
			noAccess: words['signIn.noAccess'],
			signingIn: words['signIn.signingIn'],
			inOtherWindow: words['signIn.inOtherWindow'],
			openInTab: words['signIn.openInTab'],
			signInUnfinished: words['signIn.unfinished'],
			windowBlocked: words['signIn.windowBlocked'],
			windowBlockedSentence: words['signIn.windowBlockedSentence'],
			logOut: words['logout.button']
		});
	});

	it('puts the app name into the no-access sentence', () => {
		expect(signInPageWords(words).noAccessSentence('Hafen')).toBe(
			words['signIn.noAccessSentence']({ app: 'Hafen' })
		);
	});
});

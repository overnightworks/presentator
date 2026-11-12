import type { AccountMenuWords, SignInPageWords } from 'overnightworks-design';
import type { Words } from '#lib/words/de.ts';

export function accountMenuWords(words: Words): AccountMenuWords {
	return {
		theme: words['account.theme'],
		followSystem: words['account.followSystem'],
		light: words['account.light'],
		dark: words['account.dark'],
		logOut: words['logout.button']
	};
}

export function signInPageWords(words: Words): SignInPageWords {
	return {
		loggedOut: words['signIn.loggedOut'],
		logIn: words['signIn.logIn'],
		loginFailed: words['signIn.loginFailed'],
		loginFailedBanner: words['signIn.loginFailedBanner'],
		tryAgain: words['signIn.tryAgain'],
		noAccess: words['signIn.noAccess'],
		noAccessSentence: (app) => words['signIn.noAccessSentence']({ app }),
		signingIn: words['signIn.signingIn'],
		inOtherWindow: words['signIn.inOtherWindow'],
		openInTab: words['signIn.openInTab'],
		signInUnfinished: words['signIn.unfinished'],
		windowBlocked: words['signIn.windowBlocked'],
		windowBlockedSentence: words['signIn.windowBlockedSentence'],
		logOut: words['logout.button']
	};
}

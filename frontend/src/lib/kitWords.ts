import type { AccountMenuWords, SignInPageWords } from 'overnightworks-design';
import type { LobbyText } from '#lib/api/session.ts';
import { fill } from '#lib/words.ts';

export function accountMenuWords(words: LobbyText): AccountMenuWords {
	return {
		theme: words.theme_title,
		followSystem: words.theme_follow_system,
		light: words.theme_light,
		dark: words.theme_dark,
		logOut: words.log_out
	};
}

export function signInPageWords(words: LobbyText): SignInPageWords {
	return {
		loggedOut: words.sign_in_logged_out,
		logIn: words.sign_in_log_in,
		loginFailed: words.sign_in_failed,
		loginFailedBanner: words.sign_in_failed_banner,
		tryAgain: words.sign_in_try_again,
		noAccess: words.sign_in_no_access,
		noAccessSentence: (appName) => fill(words.sign_in_no_access_sentence, { app: appName }),
		signingIn: words.sign_in_signing_in,
		inOtherWindow: words.sign_in_in_other_window,
		openInTab: words.sign_in_open_in_tab,
		signInUnfinished: words.sign_in_unfinished,
		windowBlocked: words.sign_in_window_blocked,
		windowBlockedSentence: words.sign_in_window_blocked_sentence,
		logOut: words.log_out
	};
}

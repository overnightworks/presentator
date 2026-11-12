import { flushSync } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';

import type { SessionView } from '#lib/api/session.ts';
import { commitSession, languageTag, showTheme } from '#lib/session.svelte.ts';

function sessionIn(language: SessionView['language'], theme: SessionView['theme']): SessionView {
	return {
		person: { role: 'user', username: 'anna' },
		language,
		sign_in: { account_console_url: '', logout: '', start: '', talk_start: '' },
		theme
	};
}

afterEach(() => {
	delete document.documentElement.dataset.theme;
	document.documentElement.lang = '';
});

describe('the session store', () => {
	it('sets the language of the page and of the ages when the language changes, with no reload', () => {
		commitSession(sessionIn('en', 'follow-system'));
		flushSync();
		expect(document.documentElement.lang).toBe('en');

		commitSession(sessionIn('de', 'follow-system'));
		flushSync();

		expect(document.documentElement.lang).toBe('de');
		expect(languageTag()).toBe('de');
	});

	it('paints a chosen theme and hands the look back to the device when it is followed', () => {
		commitSession(sessionIn('en', 'follow-system'));
		showTheme('dark');
		flushSync();
		expect(document.documentElement.dataset.theme).toBe('dark');

		showTheme('follow-system');
		flushSync();

		expect(document.documentElement.dataset.theme).toBe('follow-system');
	});
});

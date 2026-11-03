import { getJson, putJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type AccountView = components['schemas']['AccountView'];
export type AccountWrite = components['schemas']['AccountWrite'];
export type ThemeChoice = components['schemas']['ThemeChoice'];

export const ACCOUNT_PATH = '/api/account';

export function readAccount(fetcher: typeof fetch): Promise<AccountView> {
	return getJson<AccountView>(fetcher, ACCOUNT_PATH);
}

// The write replaces both overrides, and the session shows only the effective
// language, so a theme change sends back the language override it just read.
export async function writeTheme(fetcher: typeof fetch, theme: ThemeChoice): Promise<void> {
	const { language_tag } = await readAccount(fetcher);
	await putJson<AccountView, AccountWrite>(fetcher, ACCOUNT_PATH, { language_tag, theme });
}

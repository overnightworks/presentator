import { putJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type AccountView = components['schemas']['AccountView'];
export type ThemeChoice = components['schemas']['ThemeChoice'];

const ACCOUNT_PATH = '/api/account';

export async function writeTheme(fetcher: typeof fetch, theme: ThemeChoice): Promise<void> {
	await putJson<AccountView, AccountView>(fetcher, ACCOUNT_PATH, { theme });
}

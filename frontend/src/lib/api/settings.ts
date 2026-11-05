import { getJson, putJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type SettingsView = components['schemas']['SettingsView'];
export type SettingsWrite = components['schemas']['SettingsWrite'];

export const SETTINGS_PATH = '/api/settings';

export function readSettings(fetcher: typeof fetch, here: URL): Promise<SettingsView> {
	return getJson<SettingsView>(fetcher, SETTINGS_PATH, here);
}

/** Replaces both defaults and answers what is stored. */
export function writeSettings(fetcher: typeof fetch, write: SettingsWrite): Promise<SettingsView> {
	return putJson<SettingsView, SettingsWrite>(fetcher, SETTINGS_PATH, write);
}

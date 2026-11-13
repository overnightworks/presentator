import { getJson, putJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type SettingsView = components['schemas']['SettingsView'];

const SETTINGS_PATH = '/api/settings';

export function readSettings(fetcher: typeof fetch, here: URL): Promise<SettingsView> {
	return getJson<SettingsView>(fetcher, SETTINGS_PATH, here);
}

/** Replaces the settings and answers what is stored. */
export function writeSettings(fetcher: typeof fetch, write: SettingsView): Promise<SettingsView> {
	return putJson<SettingsView, SettingsView>(fetcher, SETTINGS_PATH, write);
}

import { afterEach, describe, expect, it, vi } from 'vitest';

import { documentNavigation, signInStartsAt } from '#lib/api/client.ts';
import { load } from './+page';

afterEach(() => {
	vi.restoreAllMocks();
});

// A client navigation into the page (Back from General or a deck) runs the
// load before the window's address changes, so the two differ.
const loadsFor = new URL('http://lobby.test/settings/sources/talks?x=1');

describe('the source page load', () => {
	it("leaves for the sign-in with the load's own address after a 401", async () => {
		const navigated = vi.spyOn(documentNavigation, 'to').mockImplementation(() => undefined);
		signInStartsAt('/login/oidc');
		const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(null, { status: 401 }));

		void load({ fetch, params: { name: 'talks' }, url: loadsFor } as unknown as Parameters<
			typeof load
		>[0]);

		await vi.waitFor(() =>
			expect(navigated).toHaveBeenCalledWith(
				`/login/oidc?next=${encodeURIComponent('/settings/sources/talks?x=1')}`
			)
		);
	});
});

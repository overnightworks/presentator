import { afterEach, describe, expect, it, vi } from 'vitest';

import { documentNavigation, signInStartsAt } from '#lib/api/client.ts';
import { load } from './+page';

afterEach(() => {
	vi.restoreAllMocks();
});

const here = new URL('http://lobby.test/settings/sources/new?x=1');

function loadWith(answer: Response) {
	const fetch = vi.fn<typeof globalThis.fetch>(async () => answer);
	const result = load({ fetch, url: here } as unknown as Parameters<typeof load>[0]);
	return { result, fetch };
}

describe('the Add source page load', () => {
	it("reads the admin's key draft with a POST and hands it to the page", async () => {
		const { result, fetch } = loadWith(Response.json({ id: 'draft-a', public_key: 'key-a' }));

		await expect(result).resolves.toEqual({ draft: { id: 'draft-a', public_key: 'key-a' } });
		expect(fetch.mock.calls[0]?.[1]?.method).toBe('POST');
	});

	it("passes a member's refusal on for the root error page", async () => {
		await expect(loadWith(new Response(null, { status: 403 })).result).rejects.toMatchObject({
			status: 403
		});
	});

	it("leaves for the sign-in with the load's own address after a 401", async () => {
		const navigated = vi.spyOn(documentNavigation, 'to').mockImplementation(() => undefined);
		signInStartsAt('/login/oidc');

		void loadWith(new Response(null, { status: 401 }));

		await vi.waitFor(() =>
			expect(navigated).toHaveBeenCalledWith(
				`/login/oidc?next=${encodeURIComponent('/settings/sources/new?x=1')}`
			)
		);
	});
});

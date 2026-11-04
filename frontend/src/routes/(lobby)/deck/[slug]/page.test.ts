import { afterEach, describe, expect, it, vi } from 'vitest';

import { documentNavigation, signInStartsAt } from '#lib/api/client.ts';
import { load } from './+page';

afterEach(() => {
	vi.restoreAllMocks();
});

const here = new URL('http://lobby.test/deck/a%20b?x=1');

function loadWith(answer: Response, slug = 'a b') {
	const fetch = vi.fn<typeof globalThis.fetch>(async () => answer);
	const result = load({ fetch, params: { slug }, url: here } as unknown as Parameters<typeof load>[0]);
	return { result, fetch };
}

describe('the deck page load', () => {
	it('reads the deck under its slug, encoded as one path segment', async () => {
		const { result, fetch } = loadWith(
			Response.json({ slug: 'a?b#c', title: 'A deck' }),
			'a?b#c/%'
		);

		await expect(result).resolves.toEqual({ deck: { slug: 'a?b#c', title: 'A deck' } });
		expect(fetch.mock.calls[0]?.[0]).toBe('/api/decks/a%3Fb%23c%2F%25');
	});

	it('answers no deck for a 404, so the page draws its own board', async () => {
		await expect(loadWith(new Response(null, { status: 404 })).result).resolves.toEqual({ deck: null });
	});

	it('rethrows a refusal that is not a 404 for the root error page', async () => {
		await expect(loadWith(new Response(null, { status: 403 })).result).rejects.toMatchObject({
			status: 403
		});
	});

	it('rethrows a failure that is no refusal, so a 500 never reads as an unknown deck', async () => {
		await expect(loadWith(new Response(null, { status: 500 })).result).rejects.toThrow('500');
	});

	it("leaves for the sign-in with the load's own address after a 401", async () => {
		const navigated = vi.spyOn(documentNavigation, 'to').mockImplementation(() => undefined);
		signInStartsAt('/login/oidc');

		void loadWith(new Response(null, { status: 401 }));

		await vi.waitFor(() =>
			expect(navigated).toHaveBeenCalledWith(
				`/login/oidc?next=${encodeURIComponent('/deck/a%20b?x=1')}`
			)
		);
	});
});


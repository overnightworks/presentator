import { describe, expect, it } from 'vitest';

import { getJson } from '#lib/api/client.ts';

function answering(status: number, body: unknown): { fetcher: typeof fetch; asked: Request[] } {
	const asked: Request[] = [];
	const fetcher: typeof fetch = async (input, init) => {
		asked.push(new Request(new URL(String(input), 'http://lobby.test'), init));
		return new Response(JSON.stringify(body), { status });
	};
	return { fetcher, asked };
}

describe('getJson', () => {
	it('asks for JSON, so the session gate answers 401 instead of a sign-in redirect', async () => {
		const { fetcher, asked } = answering(200, {});
		await getJson(fetcher, '/api/session');
		expect(asked[0]?.headers.get('accept')).toBe('application/json');
	});

	it('returns the answer the route sent', async () => {
		const { fetcher } = answering(200, { theme: 'dark' });
		await expect(getJson(fetcher, '/api/session')).resolves.toEqual({ theme: 'dark' });
	});

	it('fails loud on an answer that is not a success', async () => {
		const { fetcher } = answering(401, {});
		await expect(getJson(fetcher, '/api/decks')).rejects.toThrow('/api/decks answered 401');
	});
});

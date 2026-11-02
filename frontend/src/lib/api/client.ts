// The lobby's one transport. Every call asks for JSON, because the session
// gate answers a JSON caller with 401 and anyone else with a redirect to the
// sign-in (webauth's Accept negotiation).
const JSON_ACCEPT = { Accept: 'application/json' } as const;

export async function getJson<Answer>(fetcher: typeof fetch, path: string): Promise<Answer> {
	const answer = await fetcher(path, { headers: JSON_ACCEPT });
	if (!answer.ok) {
		throw new Error(`${path} answered ${answer.status}`);
	}
	return (await answer.json()) as Answer;
}

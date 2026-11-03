// The lobby's one transport. Every call asks for JSON, because the session
// gate answers a JSON caller with 401 and anyone else with a redirect to the
// sign-in (webauth's Accept negotiation).
const JSON_HEADERS = { Accept: 'application/json' } as const;

/** The one place a page leaves the app by a document navigation. */
export const documentNavigation = {
	to: (address: string): void => window.location.assign(address)
};

let signInStart: string | undefined;

/** Tells the transport where sign-in starts, as the session says. */
export function signInStartsAt(address: string): void {
	signInStart = address;
}

// An ended session ends in the sign-in, whichever call met the 401. The page
// is left by a document navigation, so no unsaved state is carried over and
// the answer never settles: the load or handler behind it renders no error
// on the way out.
export function leaveForSignIn(): Promise<never> {
	if (signInStart === undefined) {
		throw new Error('a 401 came before the session said where sign-in starts');
	}
	const here = window.location.pathname + window.location.search;
	documentNavigation.to(`${signInStart}?next=${encodeURIComponent(here)}`);
	return new Promise<never>(() => undefined);
}

async function answerOf<Answer>(path: string, answer: Response): Promise<Answer> {
	if (answer.status === 401) {
		return leaveForSignIn();
	}
	if (!answer.ok) {
		throw new Error(`${path} answered ${answer.status}`);
	}
	return (await answer.json()) as Answer;
}

export async function getJson<Answer>(fetcher: typeof fetch, path: string): Promise<Answer> {
	return answerOf(path, await fetcher(path, { headers: JSON_HEADERS }));
}

export async function putJson<Answer, Body>(
	fetcher: typeof fetch,
	path: string,
	body: Body
): Promise<Answer> {
	return answerOf(
		path,
		await fetcher(path, {
			method: 'PUT',
			headers: { ...JSON_HEADERS, 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		})
	);
}

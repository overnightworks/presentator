import { error } from '@sveltejs/kit';

// The lobby's one transport. Every call asks for JSON, because the session
// gate answers a JSON caller with 401 and anyone else with a redirect to the
// sign-in (webauth's Accept negotiation).
const JSON_HEADERS = { Accept: 'application/json' } as const;

/** The one place a page leaves the app by a document navigation. */
export const documentNavigation = {
	to: (address: string): void => window.location.assign(address)
};

let signInStart: string | undefined;
let leftFor: string | undefined;

/** Tells the transport where sign-in starts, as the session says. */
export function signInStartsAt(address: string): void {
	signInStart = address;
}

// A page restored from the back/forward cache is left again by its next 401.
window.addEventListener('pagehide', () => {
	leftFor = undefined;
});

/** The address a call is made for: the load event's `url`, else the page shown. */
type Here = Pick<Location, 'pathname' | 'search'>;

// An ended session ends in the sign-in, whichever call met the 401. The page
// is left by one document navigation however many calls meet it, so no
// unsaved state is carried over, and the answer never settles: the load or
// handler behind it renders no error on the way out. A load runs before the
// address commits, so it names its own `url` as `next`.
export function leaveForSignIn(here: Here): Promise<never> {
	if (signInStart === undefined) {
		throw new Error('a 401 came before the session said where sign-in starts');
	}
	const target = `${signInStart}?next=${encodeURIComponent(here.pathname + here.search)}`;
	if (target !== leftFor) {
		leftFor = target;
		documentNavigation.to(target);
	}
	return new Promise<never>(() => undefined);
}

// The refusals the error page answers for; any other refusal fails loud.
const PAGE_REFUSALS = new Set([403, 404]);

async function answerOf<Answer>(path: string, answer: Response, here: Here): Promise<Answer> {
	if (answer.status === 401) {
		return leaveForSignIn(here);
	}
	if (PAGE_REFUSALS.has(answer.status)) {
		error(answer.status, `${path} answered ${answer.status}`);
	}
	if (!answer.ok) {
		throw new Error(`${path} answered ${answer.status}`);
	}
	return (await answer.json()) as Answer;
}

export async function getJson<Answer>(
	fetcher: typeof fetch,
	path: string,
	here: Here = window.location
): Promise<Answer> {
	return answerOf(path, await fetcher(path, { headers: JSON_HEADERS }), here);
}

export async function putJson<Answer, Body>(
	fetcher: typeof fetch,
	path: string,
	body: Body,
	here: Here = window.location
): Promise<Answer> {
	return answerOf(
		path,
		await fetcher(path, {
			method: 'PUT',
			headers: { ...JSON_HEADERS, 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		}),
		here
	);
}

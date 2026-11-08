import { error } from '@sveltejs/kit';

import type { operations } from '#lib/api/types.ts';

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

// What a create or a check answers 422 when it refuses a source for a reason a
// person can fix. The tag is typed by the generated schema, so a changed wire
// fails the type check.
type SourceRefusedProblem =
	operations['create_api_sources_post']['responses'][422]['content']['application/problem+json'];
const SOURCE_REFUSED: SourceRefusedProblem['type'] =
	'tag:hallucinai.de,2026-09-08:presentator/problems/source-refused';

/** The reasons a source is refused, as the wire names them. */
export type SourceRefusalReason = SourceRefusedProblem['reason'];

/** A 422 the page words itself: the source was refused for a reason a person can fix. */
export class Refused extends Error {
	readonly reason: SourceRefusalReason;

	constructor(path: string, problem: SourceRefusedProblem) {
		super(`${path} refused the source: ${problem.reason}`);
		this.reason = problem.reason;
	}
}

// The problem a route answers when it took nothing and gives no reason.
const DECLINED = 'about:blank';

/** A 422 the server explained with no reason: nothing was taken. */
export class Declined extends Error {
	constructor(path: string) {
		super(`${path} declined`);
	}
}

// What a removal answers 409 when the counts a person confirmed are no longer
// the source's, and 500 when a mirror or build would not go. Typed by the
// generated schema, like the refused tag.
type RemovalProblems = operations['remove_api_sources__name__delete']['responses'];
type RemovalStaleProblem = RemovalProblems[409]['content']['application/problem+json'];
const REMOVAL_STALE: RemovalStaleProblem['type'] =
	'tag:hallucinai.de,2026-09-08:presentator/problems/source-removal-stale';
const REMOVAL_REFUSED: RemovalProblems[500]['content']['application/problem+json']['type'] =
	'tag:hallucinai.de,2026-09-08:presentator/problems/source-removal-refused';

/** What a removal would take with it, as the wire counts it. */
export interface RemovalCounts {
	deck_count: number;
	run_count: number;
}

/** A 409: the counts changed since they were shown; nothing was removed. */
export class RemovalStale extends Error {
	readonly counts: RemovalCounts;

	constructor(path: string, problem: RemovalStaleProblem) {
		super(`${path} found other counts than the confirmed ones`);
		this.counts = { deck_count: problem.deck_count, run_count: problem.run_count };
	}
}

/** A 500: a mirror or build would not go, so every row is kept. */
export class RemovalRefused extends Error {
	constructor(path: string) {
		super(`${path} refused the removal`);
	}
}

// The refusals the error page answers for; any other refusal fails loud.
const PAGE_REFUSALS = new Set([403, 404]);

const problemOf = (answer: Response): Promise<{ type?: string }> =>
	answer.json().catch(() => ({}));

// The two problems a removal answers for; either is the caller's to show.
async function removalProblem(path: string, answer: Response): Promise<void> {
	if (answer.status !== 409 && answer.status !== 500) {
		return;
	}
	const problem = await problemOf(answer);
	if (answer.status === 409 && problem.type === REMOVAL_STALE) {
		throw new RemovalStale(path, problem as RemovalStaleProblem);
	}
	if (answer.status === 500 && problem.type === REMOVAL_REFUSED) {
		throw new RemovalRefused(path);
	}
}

async function answerOf<Answer>(path: string, answer: Response, here: Here): Promise<Answer> {
	if (answer.status === 401) {
		return leaveForSignIn(here);
	}
	if (PAGE_REFUSALS.has(answer.status)) {
		error(answer.status, `${path} answered ${answer.status}`);
	}
	await removalProblem(path, answer);
	if (answer.status === 422) {
		const problem = await problemOf(answer);
		if (problem.type === SOURCE_REFUSED) {
			throw new Refused(path, problem as SourceRefusedProblem);
		}
		if (problem.type === DECLINED) {
			throw new Declined(path);
		}
	}
	if (!answer.ok) {
		throw new Error(`${path} answered ${answer.status}`);
	}
	if (answer.status === 204) {
		return undefined as Answer;
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

/** A POST; a body-less one passes `undefined`. */
export async function postJson<Answer, Body = undefined>(
	fetcher: typeof fetch,
	path: string,
	body: Body,
	here: Here = window.location
): Promise<Answer> {
	return answerOf(
		path,
		await fetcher(path, {
			method: 'POST',
			headers: { ...JSON_HEADERS, 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		}),
		here
	);
}

export async function deleteJson<Answer>(
	fetcher: typeof fetch,
	path: string,
	here: Here = window.location
): Promise<Answer> {
	return answerOf(path, await fetcher(path, { method: 'DELETE', headers: JSON_HEADERS }), here);
}

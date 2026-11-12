import { describe, expect, it } from 'vitest';

import type { SourceRefusalReason } from '#lib/api/client.ts';
import type { ConnectionCheckView, SourceFetchView } from '#lib/api/sources.ts';
import type { AccessKind, SourceRunView, SourceState } from '#lib/api/sources.ts';
import {
	accessView,
	checkBanner,
	fetchOutcome,
	refusalWords,
	removalBody,
	removalTitle,
	removedNotice,
	runRow,
	stateShape,
	stateWords
} from '#lib/source.ts';
import { words } from '#lib/words/en.ts';

describe('refusalWords', () => {
	it.each<{ reason: SourceRefusalReason; worded: string }>([
		{ reason: 'malformed-name', worded: words['source.refusedName'] },
		{ reason: 'malformed-folder', worded: words['source.refusedFolder'] },
		{ reason: 'duplicate-name', worded: words['source.refusedDuplicateName'] },
		{ reason: 'duplicate-url', worded: words['source.refusedDuplicateUrl'] },
		{ reason: 'unknown-access', worded: words['source.refusedUnknownAccess'] },
		{ reason: 'not-checked', worded: words['source.refusedNotChecked'] },
		{ reason: 'credential-not-allowed', worded: words['source.refusedSecretNotAllowed'] },
		{ reason: 'outside-local-mount', worded: words['source.refusedOutsideMount'] },
		{ reason: 'deploy-key-unavailable', worded: words['source.refusedDeployKey'] }
	])('words $reason by its sentence', ({ reason, worded }) => {
		expect(refusalWords(reason, words)).toBe(worded);
	});
});

const probe = (overrides: Partial<ConnectionCheckView>): ConnectionCheckView => ({
	commit: null,
	detail: null,
	failure: null,
	fingerprint: null,
	state: 'reachable',
	...overrides
});

describe('checkBanner', () => {
	it('names the head a reachable check read, as given', () => {
		expect(checkBanner(probe({ commit: 'abc12345678' }), words)).toEqual({
			shape: 'ok',
			state: words['source.stateReachable'],
			message: words['source.checkReachable']({ commit: 'abc12345678' }),
			detail: null
		});
	});

	it.each<{
		failure: NonNullable<ConnectionCheckView['failure']>;
		state: SourceState;
		stated: string;
		worded: string;
	}>([
		{
			failure: 'unreachable',
			state: 'error',
			stated: words['source.stateError'],
			worded: words['source.runUnreachable']
		},
		{
			failure: 'refused',
			state: 'refused',
			stated: words['source.stateRefused'],
			worded: words['source.runRefused']
		},
		{
			failure: 'failed',
			state: 'failed',
			stated: words['source.stateFailed'],
			worded: words['source.runFailed']
		},
		{
			failure: 'credential-unresolvable',
			state: 'error',
			stated: words['source.stateError'],
			worded: words['source.runSecret']
		}
	])('words a $failure check by its state and reason', ({ failure, state, stated, worded }) => {
		expect(checkBanner(probe({ failure, state }), words)).toEqual({
			shape: 'fail',
			state: stated,
			message: worded,
			detail: null
		});
	});

	it("carries git's own line verbatim, markup included", () => {
		const detail = 'fatal: <b>not found</b> & gone';

		expect(checkBanner(probe({ failure: 'failed', detail }), words).detail).toBe(detail);
	});
});

describe('the state of a source', () => {
	it.each<{ state: SourceState; shape: string; worded: string }>([
		{ state: 'reachable', shape: 'ready', worded: words['source.stateReachable'] },
		{ state: 'never-fetched', shape: 'never-built', worded: words['source.stateNeverFetched'] },
		{ state: 'error', shape: 'failed', worded: words['source.stateError'] },
		{ state: 'refused', shape: 'failed', worded: words['source.stateRefused'] },
		{ state: 'failed', shape: 'failed', worded: words['source.stateFailed'] }
	])('draws $state as $shape in its word', ({ state, shape, worded }) => {
		expect(stateShape(state)).toBe(shape);
		expect(stateWords(state, words)).toBe(worded);
	});
});

const run = (overrides: Partial<SourceRunView>): SourceRunView => ({
	at: '2026-11-04T10:00:00Z',
	commit: null,
	outcome: 'failure',
	reason: null,
	state: 'error',
	...overrides
});

describe('runRow', () => {
	it('words a success by the fetched word and carries its short commit', () => {
		expect(runRow(run({ outcome: 'success', state: 'reachable', commit: 'a3f19c2' }), words)).toEqual({
			shape: 'ready',
			state: words['source.runFetched'],
			detail: { kind: 'commit', text: 'a3f19c2' }
		});
	});

	it.each<{ reason: NonNullable<SourceRunView['reason']>; worded: string }>([
		{ reason: 'unreachable', worded: words['source.runUnreachable'] },
		{ reason: 'refused', worded: words['source.runRefused'] },
		{ reason: 'failed', worded: words['source.runFailed'] },
		{ reason: 'credential-unresolvable', worded: words['source.runSecret'] }
	])('words a failure for $reason by its reason', ({ reason, worded }) => {
		expect(runRow(run({ reason }), words)).toEqual({
			shape: 'failed',
			state: words['source.stateError'],
			detail: { kind: 'reason', text: worded }
		});
	});

	it('carries no detail for a failure that gave no reason', () => {
		expect(runRow(run({}), words).detail).toBeNull();
	});
});

const fetched = (overrides: Partial<SourceFetchView>): SourceFetchView => ({
	run: { at: '2026-11-09T10:00:00Z', commit: 'abc1234', outcome: 'success', reason: null, state: 'reachable' },
	decks_built: 0,
	builds_failed: 0,
	decks_removed: 0,
	...overrides
});

describe('fetchOutcome', () => {
	const built = (count: number) => words['source.fetchBuilt']({ count });
	const broke = (count: number) => words['source.fetchFailed']({ count });
	const gone = (count: number) => words['source.fetchRemoved']({ count });

	it.each<{ answer: Partial<SourceFetchView>; shape: string; text: string }>([
		{ answer: { decks_built: 1 }, shape: 'ready', text: `${words['source.fetchChanged']} ${built(1)}` },
		{
			answer: { decks_built: 1, builds_failed: 1 },
			shape: 'failed',
			text: `${words['source.fetchChanged']} ${built(1)}, ${broke(1)}`
		},
		{ answer: { decks_removed: 2 }, shape: 'ready', text: `${words['source.fetchChanged']} ${gone(2)}` },
		{
			answer: { decks_built: 3, builds_failed: 2, decks_removed: 1 },
			shape: 'failed',
			text: `${words['source.fetchChanged']} ${built(3)}, ${broke(2)}, ${gone(1)}`
		},
		{ answer: {}, shape: 'ready', text: words['source.fetchNothingNew'] },
		{ answer: { run: null }, shape: 'never-built', text: words['source.fetchBusy'] },
		{
			answer: {
				run: { at: '2026-11-09T10:00:00Z', commit: null, outcome: 'failure', reason: 'unreachable', state: 'error' }
			},
			shape: 'failed',
			text: words['source.runUnreachable']
		},
		{
			answer: {
				run: { at: '2026-11-09T10:00:00Z', commit: null, outcome: 'failure', reason: null, state: 'error' }
			},
			shape: 'failed',
			text: words['source.runFailed']
		}
	])('words a fetch that ended with $answer in the $shape shape', ({ answer, shape, text }) => {
		expect(fetchOutcome(fetched(answer), words)).toEqual({ shape, text });
	});
});

describe('accessView', () => {
	it.each<{ access: AccessKind | null; control: string; tag: string | null }>([
		{ access: 'ssh-deploy-key', control: 'deploy-key', tag: words['source.accessDeployKey'] },
		{ access: 'local-folder', control: 'none', tag: words['source.accessLocal'] },
		{ access: 'https-token', control: 'token', tag: words['source.accessToken'] },
		{ access: null, control: 'token', tag: null }
	])('shows the $control control for $access', ({ access, control, tag }) => {
		expect(accessView(access, words)).toEqual({ control, tag });
	});
});

describe('the removal sentences', () => {
	const counts = { deck_count: 1, run_count: 2 };

	it('words what a removal takes with it: its decks, its runs, and that the URL can be added again', () => {
		const body = removalBody(counts, words);

		expect(body).toContain(words['source.removeConfirmDecks'](1));
		expect(body).toContain(words['source.removeConfirmRuns'](2));
		expect(body).toContain(words['source.removeConfirmUrl']);
	});

	it('words what a removal took with it, with the name as given', () => {
		const notice = removedNotice('Talks', counts, words);

		expect(notice).toContain(words['source.removedName']({ name: 'Talks' }));
		expect(notice).toContain(words['source.removedDecks'](1));
		expect(notice).toContain(words['source.removedRuns'](2));
		expect(notice).toContain(words['source.removedUrl']);
	});
});

describe('removalTitle', () => {
	it('splits the heading where the name stands', () => {
		const { before, after } = removalTitle(words);

		expect(`${before}Talks${after}`).toBe(words['source.removeConfirmTitle']({ name: 'Talks' }));
	});
});

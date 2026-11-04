import { describe, expect, it } from 'vitest';

import type { SourceRefusalReason } from '#lib/api/client.ts';
import type { LobbyText } from '#lib/api/session.ts';
import type { ConnectionCheckView } from '#lib/api/sources.ts';
import type { AccessKind, SourceRunView, SourceState } from '#lib/api/sources.ts';
import {
	accessView,
	checkBanner,
	refusalWords,
	removalBody,
	removalTitle,
	removedNotice,
	runRow,
	stateShape,
	stateWords
} from '#lib/source.ts';

// A scenario's own templates: none of them is the catalog's sentence.
const words = {
	source_refused_name: 'a name that cannot be',
	source_refused_duplicate_name: 'that name is taken',
	source_refused_duplicate_url: 'that address is taken',
	source_refused_access: 'the access does not fit the address',
	source_refused_secret: 'a secret is missing',
	source_refused_not_checked: 'check first',
	source_refused_secret_not_allowed: 'no secret here',
	source_refused_outside_mount: 'outside the mount',
	source_refused_deploy_key: 'the key is gone',
	source_check_reachable: 'head is {commit}',
	source_run_unreachable: 'no route',
	source_run_refused: 'turned away',
	source_run_failed: 'broke',
	source_run_secret: 'the secret did not resolve',
	source_run_fetched: 'taken in',
	source_state_reachable: 'up',
	source_state_error: 'erroring',
	source_state_refused: 'rebuffed',
	source_state_failed: 'broken',
	source_state_never_fetched: 'untouched',
	source_access_token: 'a token',
	source_access_deploy_key: 'a key pair',
	source_access_local: 'this box',
	source_remove_confirm_body: '{decks} {deck_word} and {talk_word}, {runs} {run_word} go',
	source_remove_deck_singular: 'one-deck',
	source_remove_deck_plural: 'many-decks',
	source_remove_talk_singular: 'its talk',
	source_remove_talk_plural: 'their talks',
	source_remove_run_singular: 'one-run',
	source_remove_run_plural: 'many-runs',
	source_remove_confirm_title: 'Drop {name} for good?',
	source_removed: '{name} left with {decks} {deck_word} and {runs} {run_word}'
} as LobbyText;

describe('refusalWords', () => {
	it.each<{ reason: SourceRefusalReason; worded: string }>([
		{ reason: 'malformed-name', worded: 'a name that cannot be' },
		{ reason: 'duplicate-name', worded: 'that name is taken' },
		{ reason: 'duplicate-url', worded: 'that address is taken' },
		{ reason: 'access-mismatch', worded: 'the access does not fit the address' },
		{ reason: 'missing-secret', worded: 'a secret is missing' },
		{ reason: 'not-checked', worded: 'check first' },
		{ reason: 'credential-not-allowed', worded: 'no secret here' },
		{ reason: 'outside-local-mount', worded: 'outside the mount' },
		{ reason: 'deploy-key-unavailable', worded: 'the key is gone' }
	])('words $reason by the sentence given', ({ reason, worded }) => {
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
			state: 'up',
			message: 'head is abc12345678',
			detail: null
		});
	});

	it.each<{
		failure: NonNullable<ConnectionCheckView['failure']>;
		state: SourceState;
		stated: string;
		worded: string;
	}>([
		{ failure: 'unreachable', state: 'error', stated: 'erroring', worded: 'no route' },
		{ failure: 'refused', state: 'refused', stated: 'rebuffed', worded: 'turned away' },
		{ failure: 'failed', state: 'failed', stated: 'broken', worded: 'broke' },
		{
			failure: 'credential-unresolvable',
			state: 'error',
			stated: 'erroring',
			worded: 'the secret did not resolve'
		}
	])('words a $failure check by the state and reason given', ({ failure, state, stated, worded }) => {
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
		{ state: 'reachable', shape: 'ready', worded: 'up' },
		{ state: 'never-fetched', shape: 'never-built', worded: 'untouched' },
		{ state: 'error', shape: 'failed', worded: 'erroring' },
		{ state: 'refused', shape: 'failed', worded: 'rebuffed' },
		{ state: 'failed', shape: 'failed', worded: 'broken' }
	])('draws $state as $shape in the word given', ({ state, shape, worded }) => {
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
			state: 'taken in',
			detail: { kind: 'commit', text: 'a3f19c2' }
		});
	});

	it.each<{ reason: NonNullable<SourceRunView['reason']>; worded: string }>([
		{ reason: 'unreachable', worded: 'no route' },
		{ reason: 'refused', worded: 'turned away' },
		{ reason: 'failed', worded: 'broke' },
		{ reason: 'credential-unresolvable', worded: 'the secret did not resolve' }
	])('words a failure for $reason by the reason given', ({ reason, worded }) => {
		expect(runRow(run({ reason }), words)).toEqual({
			shape: 'failed',
			state: 'erroring',
			detail: { kind: 'reason', text: worded }
		});
	});

	it('carries no detail for a failure that gave no reason', () => {
		expect(runRow(run({}), words).detail).toBeNull();
	});
});

describe('accessView', () => {
	it.each<{ access: AccessKind | null; control: string; tag: string | null }>([
		{ access: 'ssh-deploy-key', control: 'deploy-key', tag: 'a key pair' },
		{ access: 'local-folder', control: 'none', tag: 'this box' },
		{ access: 'https-token', control: 'token', tag: 'a token' },
		{ access: null, control: 'token', tag: null }
	])('shows the $control control for $access', ({ access, control, tag }) => {
		expect(accessView(access, words)).toEqual({ control, tag });
	});
});

describe('the removal sentences', () => {
	// One deck with two runs: the talk word follows the deck count, the run word the run count.
	const counts = { deck_count: 1, run_count: 2 };

	it('words what a removal takes with it, singular for exactly one', () => {
		expect(removalBody(counts, words)).toBe('1 one-deck and its talk, 2 many-runs go');
	});

	it('words what a removal took with it, with the name as given', () => {
		expect(removedNotice('Talks', counts, words)).toBe('Talks left with 1 one-deck and 2 many-runs');
	});

	it('words a count of none in the plural', () => {
		expect(removalBody({ deck_count: 0, run_count: 1 }, words)).toBe(
			'0 many-decks and their talks, 1 one-run go'
		);
	});
});

describe('removalTitle', () => {
	it('splits the heading where the name stands', () => {
		expect(removalTitle(words)).toEqual({ before: 'Drop ', after: ' for good?' });
	});
});

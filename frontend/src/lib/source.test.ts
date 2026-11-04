import { describe, expect, it } from 'vitest';

import type { SourceRefusalReason } from '#lib/api/client.ts';
import type { LobbyText } from '#lib/api/session.ts';
import type { ConnectionCheckView } from '#lib/api/sources.ts';
import { checkBanner, refusalWords } from '#lib/source.ts';

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
	source_run_secret: 'the secret did not resolve'
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
			message: 'head is abc12345678',
			detail: null
		});
	});

	it.each<{ failure: NonNullable<ConnectionCheckView['failure']>; worded: string }>([
		{ failure: 'unreachable', worded: 'no route' },
		{ failure: 'refused', worded: 'turned away' },
		{ failure: 'failed', worded: 'broke' },
		{ failure: 'credential-unresolvable', worded: 'the secret did not resolve' }
	])('words a $failure check by the reason given', ({ failure, worded }) => {
		expect(checkBanner(probe({ failure }), words)).toEqual({
			shape: 'fail',
			message: worded,
			detail: null
		});
	});

	it("carries git's own line verbatim, markup included", () => {
		const detail = 'fatal: <b>not found</b> & gone';

		expect(checkBanner(probe({ failure: 'failed', detail }), words).detail).toBe(detail);
	});
});

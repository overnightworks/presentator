import type { RemovalCounts, SourceRefusalReason } from '#lib/api/client.ts';
import type {
	AccessKind,
	ConnectionCheckView,
	SourceFetchView,
	SourceRunFailure,
	SourceRunView,
	SourceState
} from '#lib/api/sources.ts';
import type { Words } from '#lib/words/de.ts';

/** The catalog's sentence for each reason a source is refused. */
export function refusalWords(reason: SourceRefusalReason, words: Words): string {
	const sentences: Record<SourceRefusalReason, string> = {
		'malformed-name': words['source.refusedName'],
		'malformed-folder': words['source.refusedFolder'],
		'duplicate-name': words['source.refusedDuplicateName'],
		'duplicate-url': words['source.refusedDuplicateUrl'],
		'unknown-access': words['source.refusedUnknownAccess'],
		'not-checked': words['source.refusedNotChecked'],
		'credential-not-allowed': words['source.refusedSecretNotAllowed'],
		'outside-local-mount': words['source.refusedOutsideMount'],
		'deploy-key-unavailable': words['source.refusedDeployKey']
	};
	return sentences[reason];
}

export interface CheckBanner {
	shape: 'ok' | 'fail';
	// The word the source would carry in the list.
	state: string;
	message: string;
	// Git's own first line, which only a failed check carries.
	detail: string | null;
}

/** What a probe found, in the words a source's runs are told in. */
export function checkBanner(result: ConnectionCheckView, words: Words): CheckBanner {
	if (result.failure === null) {
		return {
			shape: 'ok',
			state: stateWords(result.state, words),
			message: words['source.checkReachable']({ commit: String(result.commit) }),
			detail: null
		};
	}
	return {
		shape: 'fail',
		state: stateWords(result.state, words),
		message: runFailureWords(result.failure, words),
		detail: result.detail
	};
}

/** The catalog's sentence for why a poll or a check failed. */
export function runFailureWords(reason: SourceRunFailure, words: Words): string {
	const sentences: Record<SourceRunFailure, string> = {
		unreachable: words['source.runUnreachable'],
		refused: words['source.runRefused'],
		failed: words['source.runFailed'],
		'credential-unresolvable': words['source.runSecret']
	};
	return sentences[reason];
}

interface FetchOutcome {
	shape: ReturnType<typeof stateShape>;
	text: string;
}

/** How a fetch ended, in the catalog's words and the shape `.state` draws. */
export function fetchOutcome(fetched: SourceFetchView, words: Words): FetchOutcome {
	const { run } = fetched;
	if (run === null) {
		return { shape: 'never-built', text: words['source.fetchBusy'] };
	}
	if (run.outcome === 'failure') {
		return { shape: 'failed', text: runFailureWords(run.reason ?? 'failed', words) };
	}
	const parts = [
		fetched.decks_built > 0 && words['source.fetchBuilt']({ count: fetched.decks_built }),
		fetched.builds_failed > 0 && words['source.fetchFailed']({ count: fetched.builds_failed }),
		fetched.decks_removed > 0 && words['source.fetchRemoved']({ count: fetched.decks_removed })
	].filter((part) => part !== false);
	if (parts.length === 0) {
		return { shape: 'ready', text: words['source.fetchNothingNew'] };
	}
	return {
		shape: fetched.builds_failed > 0 ? 'failed' : 'ready',
		text: `${words['source.fetchChanged']} ${parts.join(', ')}`
	};
}

/** The shape `.state` draws for a source's state. */
export function stateShape(state: SourceState): 'ready' | 'never-built' | 'failed' {
	if (state === 'reachable') {
		return 'ready';
	}
	return state === 'never-fetched' ? 'never-built' : 'failed';
}

/** The catalog's word for what a source is. */
export function stateWords(state: SourceState, words: Words): string {
	const sentences: Record<SourceState, string> = {
		reachable: words['source.stateReachable'],
		error: words['source.stateError'],
		refused: words['source.stateRefused'],
		failed: words['source.stateFailed'],
		'never-fetched': words['source.stateNeverFetched']
	};
	return sentences[state];
}

interface RunRow {
	shape: 'ready' | 'never-built' | 'failed';
	state: string;
	// The short commit of a success, the failure sentence of a failure.
	detail: { kind: 'commit' | 'reason'; text: string } | null;
}

/** One recent poll in the words of the run table. */
export function runRow(run: SourceRunView, words: Words): RunRow {
	let detail: RunRow['detail'] = null;
	if (run.commit !== null) {
		detail = { kind: 'commit', text: run.commit };
	} else if (run.reason !== null) {
		detail = { kind: 'reason', text: runFailureWords(run.reason, words) };
	}
	return {
		shape: stateShape(run.state),
		state: run.outcome === 'success' ? words['source.runFetched'] : stateWords(run.state, words),
		detail
	};
}

interface AccessView {
	// What the access section draws: the key and its renewal, the token form, or the tag alone.
	control: 'deploy-key' | 'token' | 'none';
	tag: string | null;
}

/** How the access section reads for a source's access; an address that names none shows the token form. */
export function accessView(access: AccessKind | null, words: Words): AccessView {
	if (access === 'ssh-deploy-key') {
		return { control: 'deploy-key', tag: words['source.accessDeployKey'] };
	}
	if (access === 'local-folder') {
		return { control: 'none', tag: words['source.accessLocal'] };
	}
	return { control: 'token', tag: access === null ? null : words['source.accessToken'] };
}

/** What a removal takes with it, in the sentences the dialog asks with. */
export function removalBody(counts: RemovalCounts, words: Words): string {
	return [
		words['source.removeConfirmDecks'](counts.deck_count),
		words['source.removeConfirmRuns'](counts.run_count),
		words['source.removeConfirmUrl']
	].join(' ');
}

/** What a finished removal took with it, in the list's one notice. */
export function removedNotice(name: string, counts: RemovalCounts, words: Words): string {
	return [
		words['source.removedName']({ name }),
		words['source.removedDecks'](counts.deck_count),
		words['source.removedRuns'](counts.run_count),
		words['source.removedUrl']
	].join(' ');
}

// A private-use character the catalog's sentence cannot contain, standing where the name goes.
const NAME_PLACE = '\uE000';

/** The dialog's heading, split where the source's name stands, so the page can set the name as typed. */
export function removalTitle(words: Words): { before: string; after: string } {
	const [before = '', after = ''] = words['source.removeConfirmTitle']({ name: NAME_PLACE }).split(NAME_PLACE);
	return { before, after };
}

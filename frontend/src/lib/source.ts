import { fill } from '#lib/words.ts';
import type { RemovalCounts, SourceRefusalReason } from '#lib/api/client.ts';
import type {
	AccessKind,
	ConnectionCheckView,
	SourceRunFailure,
	SourceRunView,
	SourceState
} from '#lib/api/sources.ts';
import type { LobbyText } from '#lib/api/session.ts';

/** The catalog's sentence for each reason a source is refused. */
export function refusalWords(reason: SourceRefusalReason, words: LobbyText): string {
	const sentences: Record<SourceRefusalReason, string> = {
		'malformed-name': words.source_refused_name,
		'malformed-folder': words.source_refused_folder,
		'duplicate-name': words.source_refused_duplicate_name,
		'duplicate-url': words.source_refused_duplicate_url,
		'unknown-access': words.source_refused_unknown_access,
		'not-checked': words.source_refused_not_checked,
		'credential-not-allowed': words.source_refused_secret_not_allowed,
		'outside-local-mount': words.source_refused_outside_mount,
		'deploy-key-unavailable': words.source_refused_deploy_key
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
export function checkBanner(result: ConnectionCheckView, words: LobbyText): CheckBanner {
	if (result.failure === null) {
		return {
			shape: 'ok',
			state: stateWords(result.state, words),
			message: fill(words.source_check_reachable, { commit: result.commit }),
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
export function runFailureWords(reason: SourceRunFailure, words: LobbyText): string {
	const sentences: Record<SourceRunFailure, string> = {
		unreachable: words.source_run_unreachable,
		refused: words.source_run_refused,
		failed: words.source_run_failed,
		'credential-unresolvable': words.source_run_secret
	};
	return sentences[reason];
}

/** The shape `.state` draws for a source's state. */
export function stateShape(state: SourceState): 'ready' | 'never-built' | 'failed' {
	if (state === 'reachable') {
		return 'ready';
	}
	return state === 'never-fetched' ? 'never-built' : 'failed';
}

/** The catalog's word for what a source is. */
export function stateWords(state: SourceState, words: LobbyText): string {
	const sentences: Record<SourceState, string> = {
		reachable: words.source_state_reachable,
		error: words.source_state_error,
		refused: words.source_state_refused,
		failed: words.source_state_failed,
		'never-fetched': words.source_state_never_fetched
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
export function runRow(run: SourceRunView, words: LobbyText): RunRow {
	let detail: RunRow['detail'] = null;
	if (run.commit !== null) {
		detail = { kind: 'commit', text: run.commit };
	} else if (run.reason !== null) {
		detail = { kind: 'reason', text: runFailureWords(run.reason, words) };
	}
	return {
		shape: stateShape(run.state),
		state: run.outcome === 'success' ? words.source_run_fetched : stateWords(run.state, words),
		detail
	};
}

interface AccessView {
	// What the access section draws: the key and its renewal, the token form, or the tag alone.
	control: 'deploy-key' | 'token' | 'none';
	tag: string | null;
}

/** How the access section reads for a source's access; an address that names none shows the token form. */
export function accessView(access: AccessKind | null, words: LobbyText): AccessView {
	if (access === 'ssh-deploy-key') {
		return { control: 'deploy-key', tag: words.source_access_deploy_key };
	}
	if (access === 'local-folder') {
		return { control: 'none', tag: words.source_access_local };
	}
	return { control: 'token', tag: access === null ? null : words.source_access_token };
}

const counted = (count: number, singular: string, plural: string): string =>
	count === 1 ? singular : plural;

/** What a removal takes with it, in the sentence the dialog asks with. */
export function removalBody(counts: RemovalCounts, words: LobbyText): string {
	return fill(words.source_remove_confirm_body, {
		decks: counts.deck_count,
		deck_word: counted(counts.deck_count, words.source_remove_deck_singular, words.source_remove_deck_plural),
		talk_word: counted(counts.deck_count, words.source_remove_talk_singular, words.source_remove_talk_plural),
		runs: counts.run_count,
		run_word: counted(counts.run_count, words.source_remove_run_singular, words.source_remove_run_plural)
	});
}

/** What a finished removal took with it, in the list's one notice. */
export function removedNotice(name: string, counts: RemovalCounts, words: LobbyText): string {
	return fill(words.source_removed, {
		name,
		decks: counts.deck_count,
		deck_word: counted(counts.deck_count, words.source_remove_deck_singular, words.source_remove_deck_plural),
		runs: counts.run_count,
		run_word: counted(counts.run_count, words.source_remove_run_singular, words.source_remove_run_plural)
	});
}

/** The dialog's heading, split where the source's name stands, so the page can set the name as typed. */
export function removalTitle(words: LobbyText): { before: string; after: string } {
	const match = /^(.*)\{name\}(.*)$/s.exec(words.source_remove_confirm_title) as RegExpExecArray;
	return { before: match[1] as string, after: match[2] as string };
}

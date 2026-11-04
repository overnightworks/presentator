import { fill } from '#lib/words.ts';
import type { SourceRefusalReason } from '#lib/api/client.ts';
import type { ConnectionCheckView } from '#lib/api/sources.ts';
import type { LobbyText } from '#lib/api/session.ts';

/** The catalog's sentence for each reason a source is refused. */
export function refusalWords(reason: SourceRefusalReason, words: LobbyText): string {
	const sentences: Record<SourceRefusalReason, string> = {
		'malformed-name': words.source_refused_name,
		'duplicate-name': words.source_refused_duplicate_name,
		'duplicate-url': words.source_refused_duplicate_url,
		'access-mismatch': words.source_refused_access,
		'missing-secret': words.source_refused_secret,
		'not-checked': words.source_refused_not_checked,
		'credential-not-allowed': words.source_refused_secret_not_allowed,
		'outside-local-mount': words.source_refused_outside_mount,
		'deploy-key-unavailable': words.source_refused_deploy_key
	};
	return sentences[reason];
}

export interface CheckBanner {
	shape: 'ok' | 'fail';
	message: string;
	// Git's own first line, which only a failed check carries.
	detail: string | null;
}

/** What a probe found, in the words a source's runs are told in. */
export function checkBanner(result: ConnectionCheckView, words: LobbyText): CheckBanner {
	if (result.failure === null) {
		return {
			shape: 'ok',
			message: fill(words.source_check_reachable, { commit: result.commit }),
			detail: null
		};
	}
	const reasons: Record<NonNullable<ConnectionCheckView['failure']>, string> = {
		unreachable: words.source_run_unreachable,
		refused: words.source_run_refused,
		failed: words.source_run_failed,
		'credential-unresolvable': words.source_run_secret
	};
	return { shape: 'fail', message: reasons[result.failure], detail: result.detail };
}

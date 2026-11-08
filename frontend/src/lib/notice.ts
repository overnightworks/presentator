import { createContext } from 'svelte';

import type { RemovalCounts } from '#lib/api/client.ts';

/** What a finished removal took with it: the facts, never a sentence. */
export interface Removal {
	name: string;
	counts: RemovalCounts;
}

/**
 * The one-time notice of a removal, held by the lobby layout and by nothing
 * else: no module state, no storage, never serialised. The source page shows
 * it once on the list it ends on; reading it takes it.
 */
export class Notice {
	#removal: Removal | null = null;

	show(removal: Removal): void {
		this.#removal = removal;
	}

	take(): Removal | null {
		const removal = this.#removal;
		this.#removal = null;
		return removal;
	}
}

export const [noticeOf, provideNotice] = createContext<Notice>();

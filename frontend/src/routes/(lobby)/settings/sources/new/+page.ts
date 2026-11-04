import { openKeyDraft } from '#lib/api/sources.ts';
import type { PageLoad } from './$types';

// A POST because the draft is minted on the first ask, and this is the one
// admin-gated call a load can make: a member's 403 becomes the root error
// page's for-admins board. Asking again answers the same draft.
export const load: PageLoad = async ({ fetch, url }) => ({
	draft: await openKeyDraft(fetch, url)
});

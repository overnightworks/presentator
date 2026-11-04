import { leaveForSignIn } from '#lib/api/client.ts';
import type { LayoutLoad } from './$types';

// A signed-out answer here comes from a load that ran again after the session
// ended, beside another load that meets the 401; both leave for the sign-in,
// so nothing below this layout reads a missing person.
export const load: LayoutLoad = async ({ parent }) => {
	const { session } = await parent();
	if (session.person === null) {
		return leaveForSignIn();
	}
	return { person: session.person };
};

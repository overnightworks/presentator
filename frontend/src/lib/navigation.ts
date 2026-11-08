import { goto } from '$app/navigation';

/** The Add page's `goto` calls, in one place a test can watch. */
export const navigation = {
	goto: (address: string, options: Parameters<typeof goto>[1]): Promise<void> =>
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- the callers pass an address `resolve` made or the router itself gave
		goto(address, options)
};

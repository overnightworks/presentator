import { afterEach, describe, expect, it, vi } from 'vitest';

import { documentNavigation, signInStartsAt } from '#lib/api/client.ts';
import type { SessionView } from '#lib/api/session.ts';
import { load } from './+layout';

afterEach(() => {
	vi.restoreAllMocks();
});

function loadWith(person: SessionView['person']): ReturnType<typeof load> {
	const parent = async () => ({ session: { person } as SessionView });
	return load({ parent } as unknown as Parameters<typeof load>[0]);
}

describe('the lobby layout load', () => {
	it('hands the signed-in person to the pages', async () => {
		await expect(loadWith({ role: 'user', username: 'anna' })).resolves.toEqual({
			person: { role: 'user', username: 'anna' }
		});
	});

	it('leaves for the sign-in and settles nothing when the parent answers no person', async () => {
		const navigated = vi.spyOn(documentNavigation, 'to').mockImplementation(() => undefined);
		signInStartsAt('/login/oidc');
		const settled = vi.fn();

		void Promise.resolve(loadWith(null)).then(settled, settled);

		await vi.waitFor(() => expect(navigated).toHaveBeenCalledTimes(1));
		await Promise.resolve();
		expect(settled).not.toHaveBeenCalled();
	});
});

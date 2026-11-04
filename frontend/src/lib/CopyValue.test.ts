import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import CopyValue from '#lib/CopyValue.svelte';

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (mounted) {
		void unmount(mounted);
	}
	mounted = undefined;
	document.body.replaceChildren();
	vi.restoreAllMocks();
});

function shown(writeText: (text: string) => Promise<void>): HTMLButtonElement {
	Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
	mounted = mount(CopyValue, {
		target: document.body,
		props: { value: 'a value to copy', label: 'Take it', labelledby: 'the-field' }
	});
	flushSync();
	return document.querySelector('button') as HTMLButtonElement;
}

describe('CopyValue', () => {
	it('puts the shown value on the clipboard', () => {
		const writeText = vi.fn(async () => undefined);

		shown(writeText).click();

		expect(writeText).toHaveBeenCalledWith('a value to copy');
	});

	it('reports a refused clipboard write instead of dropping it', async () => {
		const refusal = new Error('the clipboard said no');
		const report = vi.spyOn(console, 'error').mockImplementation(() => undefined);

		shown(async () => {
			throw refusal;
		}).click();

		await vi.waitFor(() => expect(report.mock.calls.flat()).toContain(refusal));
	});
});

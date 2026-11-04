import { describe, expect, it } from 'vitest';

import { fill } from '#lib/words.ts';

describe('fill', () => {
	it('puts each placeholder in place, a repeated one every time', () => {
		expect(fill('{a} then {b} then {a}', { a: 'one', b: 2 })).toBe('one then 2 then one');
	});

	it('leaves a sentence without placeholders alone', () => {
		expect(fill('no gaps', { a: 'one' })).toBe('no gaps');
	});
});

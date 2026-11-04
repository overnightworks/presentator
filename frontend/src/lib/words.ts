/** Puts each `{name}` of a catalog sentence in place; the wire carries every count a sentence names. */
export function fill(template: string, values: Readonly<Record<string, string | number | null>>): string {
	return template.replace(/\{(\w+)\}/g, (_placeholder, name: string) => String(values[name]));
}

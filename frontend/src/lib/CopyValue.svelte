<script lang="ts">
	// The value to copy, then the button that copies it; `labelledby` names
	// the field the value belongs to, so two Copy buttons read apart.
	let { value, label, labelledby }: { value: string; label: string; labelledby: string } = $props();

	async function copy(): Promise<void> {
		try {
			await navigator.clipboard.writeText(value);
		} catch (failure) {
			console.error('Copying to the clipboard failed', failure);
		}
	}
</script>

<div class="code" role="group" aria-labelledby={labelledby}>
	<code>{value}</code>
	<button class="btn secondary sm" type="button" onclick={copy}>{label}</button>
</div>

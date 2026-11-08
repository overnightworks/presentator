<script lang="ts">
	type Props = { title: string; explanation: string; href: string; label: string };
	let { title, explanation, href, label }: Props = $props();
</script>

<div class="note-card">
	<h1>{title}</h1>
	<p>{explanation}</p>
	<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- the callers pass an address `resolve` made -->
	<a class="btn primary" {href}>{label}</a>
</div>

<style>
	.note-card {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 12px;
		max-width: 520px;
		padding: 28px 22px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--surface);
	}
	.note-card h1 {
		margin: 0;
		font-size: 20px;
		font-weight: 400;
		letter-spacing: 0.03em;
		text-transform: none;
	}
	.note-card p {
		margin: 0;
		color: var(--text-muted);
		font-size: 14px;
	}
</style>

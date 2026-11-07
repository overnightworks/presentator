<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import LobbyHeader from '#lib/LobbyHeader.svelte';
	import type { SessionPerson } from '#lib/api/session.ts';

	const session = $derived(page.data.session);
	const words = $derived(session.words);
	// The session gate serves this shell only to a signed-in person, and the
	// lobby layout leaves for the sign-in before any page below it fails.
	const person = $derived(session.person as SessionPerson);
	// The catalog has words for these two refusals only; any other status shows
	// its number, because a false sentence is worse than none.
	const boards = $derived<Partial<Record<number, { title: string; explanation: string }>>>({
		403: { title: words.for_admins_title, explanation: words.for_admins_explanation },
		404: { title: words.address_unknown_title, explanation: words.address_unknown_explanation }
	});
	const board = $derived(boards[page.status]);
</script>

<svelte:head>
	<title>{`${board ? board.title : words.wordmark}`}</title>
</svelte:head>

<LobbyHeader {session} {person} />
<main>
	{#if board}
		<div class="note-card">
			<h1>{board.title}</h1>
			<p>{board.explanation}</p>
			<a class="btn primary" href={resolve('/(lobby)')}>{words.decks_title}</a>
		</div>
	{:else}
		<h1>{page.status}</h1>
	{/if}
</main>

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

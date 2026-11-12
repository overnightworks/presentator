<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import LobbyHeader from '#lib/LobbyHeader.svelte';
	import NoteCard from '#lib/NoteCard.svelte';
	import type { SessionPerson } from '#lib/api/session.ts';

	const session = $derived(page.data.session);
	const words = $derived(page.data.words);
	// The session gate serves this shell only to a signed-in person, and the
	// lobby layout leaves for the sign-in before any page below it fails.
	const person = $derived(session.person as SessionPerson);
	// The catalog has words for these two refusals only; any other status shows
	// its number, because a false sentence is worse than none.
	const boards = $derived<Partial<Record<number, { title: string; explanation: string }>>>({
		403: { title: words['error.adminsTitle'], explanation: words['error.adminsExplanation'] },
		404: { title: words['error.notFoundTitle'], explanation: words['error.notFoundExplanation'] }
	});
	const board = $derived(boards[page.status]);
</script>

<svelte:head>
	<title>{`${board ? board.title : words['app.name']}`}</title>
</svelte:head>

<LobbyHeader {session} {person} {words} />
<main>
	{#if board}
		<NoteCard
			title={board.title}
			explanation={board.explanation}
			href={resolve('/(lobby)')}
			label={words['error.decksLink']}
		/>
	{:else}
		<h1>{page.status}</h1>
	{/if}
</main>

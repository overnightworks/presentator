<script lang="ts">
	import { LegalLinks } from 'overnightworks-appkit';
	import { page } from '$app/state';
	import LobbyHeader from '#lib/LobbyHeader.svelte';
	import type { SessionPerson } from '#lib/api/session.ts';
	import { legalLinks } from '#lib/legal.ts';

	const session = $derived(page.data.session);
	const words = $derived(session.words);
	// The session gate serves this shell only to a signed-in person, and the
	// lobby layout leaves for the sign-in before any page below it fails.
	const person = $derived(session.person as SessionPerson);
</script>

<svelte:head>
	<title>{words.address_unknown_title}</title>
</svelte:head>

<LobbyHeader {session} {person} />
<main>
	<h1>{words.address_unknown_title}</h1>
	<p>{words.address_unknown_explanation}</p>
</main>
<footer class="bottom">
	<LegalLinks links={legalLinks(words)} />
</footer>

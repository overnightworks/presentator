<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import AccountPopover from '#lib/AccountPopover.svelte';
	import type { SessionPerson, SessionView } from '#lib/api/session.ts';
	import { ariaCurrent } from '#lib/section.ts';

	let { session, person }: { session: SessionView; person: SessionPerson } = $props();
	const words = $derived(session.words);
	const pathname = $derived(page.url.pathname);
</script>

<header class="top">
	<span class="wordmark">{words.wordmark}</span>
	<nav aria-label={words.wordmark}>
		<a href={resolve('/(lobby)')} aria-current={ariaCurrent(pathname, 'decks')}
			>{words.section_decks}</a
		>
		{#if person.role === 'admin'}
			<a
				href={resolve('/(lobby)/settings')}
				aria-current={ariaCurrent(pathname, 'settings')}>{words.section_settings}</a
			>
		{/if}
	</nav>
	<AccountPopover {session} {person} />
</header>

<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import AccountPopover from '#lib/AccountPopover.svelte';
	import type { SessionPerson, SessionView } from '#lib/api/session.ts';
	import { ariaCurrent } from '#lib/section.ts';
	import type { Words } from '#lib/words/de.ts';

	let {
		session,
		person,
		words
	}: { session: SessionView; person: SessionPerson; words: Words } = $props();
	const pathname = $derived(page.url.pathname);
</script>

<header class="top">
	<span class="wordmark">{words['app.name']}</span>
	<nav aria-label={words['app.name']}>
		<a href={resolve('/(lobby)')} aria-current={ariaCurrent(pathname, 'decks')}
			>{words['header.decks']}</a
		>
		{#if person.role === 'admin'}
			<a
				href={resolve('/(lobby)/settings')}
				aria-current={ariaCurrent(pathname, 'settings')}>{words['header.settings']}</a
			>
		{/if}
	</nav>
	<AccountPopover {session} {person} {words} />
</header>

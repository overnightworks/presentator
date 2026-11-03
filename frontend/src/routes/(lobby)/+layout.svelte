<script lang="ts">
	import { LegalLinks } from 'overnightworks-appkit';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import AccountPopover from '#lib/AccountPopover.svelte';
	import { legalLinks } from '#lib/legal.ts';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();
	const words = $derived(data.session.words);
</script>

<header class="top">
	<span class="wordmark">{words.wordmark}</span>
	<nav aria-label={words.wordmark}>
		<a href={resolve('/(lobby)')} aria-current={page.url.pathname === '/' ? 'page' : undefined}>
			{words.section_decks}
		</a>
		{#if data.person.role === 'admin'}
			<a href="/settings" rel="external" data-sveltekit-reload>{words.section_settings}</a>
		{/if}
	</nav>
	<AccountPopover session={data.session} person={data.person} />
</header>
<main>{@render children()}</main>
<footer class="bottom">
	<LegalLinks links={legalLinks(words)} />
</footer>

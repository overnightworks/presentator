<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { tabCurrent } from '#lib/section.ts';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();
	const words = $derived(data.session.words);
</script>

<nav class="tabs" aria-label={words.section_settings}>
	<a href={resolve('/(lobby)/settings')} aria-current={tabCurrent(page.url.pathname, 'general')}
		>{words.settings_title}</a
	>
	<a
		href="/settings/sources"
		rel="external"
		data-sveltekit-reload
		aria-current={tabCurrent(page.url.pathname, 'sources')}>{words.settings_sources}</a
	>
</nav>
{@render children()}

<style>
	.tabs {
		display: flex;
		gap: 2px;
		margin-bottom: 20px;
		border-bottom: 1px solid var(--border);
	}
	.tabs a {
		display: inline-flex;
		align-items: center;
		min-height: 36px;
		padding: 0 8px;
		border-bottom: 2px solid transparent;
		color: var(--text-muted);
		font-family: var(--font-display);
		font-size: 12px;
		letter-spacing: 0.08em;
		text-decoration: none;
		text-transform: uppercase;
	}
	.tabs a[aria-current='page'] {
		border-bottom-color: var(--accent);
		color: var(--text);
	}
</style>

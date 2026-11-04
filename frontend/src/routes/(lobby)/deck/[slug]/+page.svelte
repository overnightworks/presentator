<script lang="ts">
	import { resolve } from '$app/paths';
	import { ageInWords, durationInWords } from '#lib/age.ts';
	import { buildsWith, failureWords, fill, stateWords } from '#lib/deck.ts';
	import { languageTag } from '#lib/session.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);
	const tag = $derived(languageTag());
	const title = $derived(data.deck === null ? words.deck_unknown_title : data.deck.title);
	const home = resolve('/(lobby)');
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${title}`}</title>
</svelte:head>

{#if data.deck === null}
	<div class="note-card">
		<h1>{words.deck_unknown_title}</h1>
		<p>{words.deck_unknown_explanation}</p>
		<a class="btn primary" href={home}>{words.decks_title}</a>
	</div>
{:else}
	{@const deck = data.deck}
	{@const attempt = deck.attempt}
	<a class="back" href={home}>{words.deck_back}</a>
	<h1 class="as-typed">{deck.title}</h1>
	<code>{deck.slug}</code>
	<section class="status">
		<h2>{words.deck_status}</h2>
		<div class="meta">
			<span class="state" data-state={deck.state}><i></i>{stateWords(words)[deck.state]}</span>
			<span>
				{#if deck.source}<code>{deck.source}</code>{/if}
				<code>{deck.commit}</code>
			</span>
			{#if deck.built_at}
				<span>{fill(words.deck_built, { age: ageInWords(deck.built_at, Date.now(), tag) })}</span>
			{/if}
		</div>
		{#if deck.themes}
			<p class="muted">{buildsWith(deck.themes, words)}</p>
		{/if}
	</section>
	{#if attempt}
		{@const failure = failureWords(attempt.failure, words)}
		<aside class="banner" data-banner={deck.state}>
			<span class="state" data-state={deck.state}
				><i></i>{deck.state === 'building' ? words.deck_building_label : words.deck_failed_title}</span
			>
			<div>
				{#if deck.state === 'building'}
					<p>
						<code>{attempt.commit}</code>
						{fill(words.deck_building_for, {
							age: durationInWords(attempt.started_at, Date.now(), tag)
						})}
					</p>
					<p>{words.deck_building_explanation}</p>
				{:else}
					<p>
						<b>{words.deck_attempt_label}</b>
						<code>{attempt.commit}</code>
						{ageInWords(attempt.started_at, Date.now(), tag)}
					</p>
					{#if failure === null}
						<p>{words.deck_failed_without_a_message}</p>
					{:else}
						<pre>{failure}</pre>
					{/if}
					{#if deck.built_at}
						<p>{words.deck_failed_last_talk_stands}</p>
					{/if}
				{/if}
			</div>
		</aside>
	{/if}
	{#if deck.built_at}
		<div class="actions">
			{#if deck.views_open}
				<a class="btn primary" href={`${deck.talk}presenter/`} rel="external"
					>{words.deck_presenter_view}</a
				>
				<a class="btn secondary" href={deck.talk} rel="external">{words.deck_projector_view}</a>
			{:else}
				<span class="btn primary off">{words.deck_presenter_view}</span>
				<span class="btn secondary off">{words.deck_projector_view}</span>
			{/if}
			<a
				class="btn secondary"
				href={`/deck/${encodeURIComponent(deck.slug)}/pdf`}
				rel="external"
				data-sveltekit-reload>{words.deck_pdf}</a
			>
		</div>
	{:else}
		<p class="muted">{words.deck_not_built_explanation}</p>
	{/if}
{/if}

<style>
	h1 {
		margin: 8px 0 0;
		font-size: 22px;
		overflow-wrap: anywhere;
	}
	.back {
		font-size: 14px;
		text-underline-offset: 3px;
	}
	.status {
		display: flex;
		flex-direction: column;
		gap: 10px;
		margin: 16px 0;
		padding-top: 16px;
		border-top: 1px solid var(--border);
	}
	.status h2 {
		margin: 0;
		font-size: 13px;
		font-weight: 400;
		letter-spacing: 0.1em;
		color: var(--text-muted);
	}
	.meta {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 16px;
		font-size: 14px;
		color: var(--text-muted);
	}
	.meta code {
		color: var(--text);
		overflow-wrap: anywhere;
	}
	.muted {
		margin: 0;
		font-size: 14px;
		color: var(--text-muted);
	}
	.banner {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		margin-bottom: 16px;
		padding: 10px 12px;
		border: 1px solid var(--fail);
		border-left-width: 3px;
		border-radius: 4px;
		background: var(--fail-soft);
		font-size: 13.5px;
	}
	.banner[data-banner='building'] {
		border-color: var(--busy);
		background: var(--busy-soft);
	}
	.banner .state {
		margin-top: 3px;
	}
	.banner p {
		margin: 0 0 4px;
	}
	.banner pre {
		margin: 4px 0;
		font-family: ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace;
		font-size: 0.85em;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		align-items: center;
	}
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-height: 36px;
		padding: 0 14px;
		border: 1px solid transparent;
		border-radius: 4px;
		font-family: var(--font-display);
		font-size: 14px;
		letter-spacing: 0.05em;
		text-decoration: none;
		text-transform: uppercase;
		white-space: nowrap;
	}
	.btn.primary {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--on-accent);
	}
	.btn.secondary {
		border-color: var(--border);
		color: var(--text);
	}
	.btn.off {
		opacity: 0.5;
	}
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

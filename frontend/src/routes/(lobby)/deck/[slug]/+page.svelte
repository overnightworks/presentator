<script lang="ts">
	import { resolve } from '$app/paths';
	import NoteCard from '#lib/NoteCard.svelte';
	import { ageInWords, durationInWords } from '#lib/age.ts';
	import { buildsWith, failureWords, stateWords } from '#lib/deck.ts';
	import { languageTag } from '#lib/session.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.words);
	const tag = $derived(languageTag());
	const title = $derived(data.deck === null ? words['deck.unknownTitle'] : data.deck.title);
	const home = resolve('/(lobby)');
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${title}`}</title>
</svelte:head>

{#if data.deck === null}
	<NoteCard
		title={words['deck.unknownTitle']}
		explanation={words['deck.unknownExplanation']}
		href={home}
		label={words['deck.decksLink']}
	/>
{:else}
	{@const deck = data.deck}
	{@const attempt = deck.attempt}
	<a class="back" href={home}>{words['deck.back']}</a>
	<h1 class="as-typed">{deck.title}</h1>
	<code>{deck.slug}</code>
	<section class="status">
		<h2>{words['deck.status']}</h2>
		<div class="meta">
			<span class="state" data-state={deck.state}><i></i>{stateWords(words)[deck.state]}</span>
			<span>
				{#if deck.source}<code>{deck.source}</code>{/if}
				<code>{deck.commit}</code>
			</span>
			{#if deck.built_at}
				<span>{words['deck.built']({ age: ageInWords(deck.built_at, Date.now(), tag) })}</span>
			{/if}
		</div>
		{#if deck.themes}
			<p class="muted">{buildsWith(deck.themes, words, tag)}</p>
		{/if}
	</section>
	{#if attempt}
		{@const failure = failureWords(attempt.failure, words)}
		<aside class="banner" data-banner={deck.state}>
			<span class="state" data-state={deck.state}
				><i></i>{deck.state === 'building' ? words['deck.buildingLabel'] : words['deck.failedTitle']}</span
			>
			<div>
				{#if deck.state === 'building'}
					<p>
						<code>{attempt.commit}</code>
						{words['deck.buildingFor']({
							age: durationInWords(attempt.started_at, Date.now(), tag)
						})}
					</p>
					<p>{words['deck.buildingExplanation']}</p>
				{:else}
					<p>
						<b>{words['deck.attemptLabel']}</b>
						<code>{attempt.commit}</code>
						{ageInWords(attempt.started_at, Date.now(), tag)}
					</p>
					{#if failure === null}
						<p>{words['deck.failedWithoutMessage']}</p>
					{:else}
						<pre>{failure}</pre>
					{/if}
				{/if}
			</div>
		</aside>
		{#if deck.state !== 'building' && deck.built_at}
			<p class="muted standing">{words['deck.failedLastTalkStands']}</p>
		{/if}
	{/if}
	{#if deck.built_at}
		<div class="actions">
			{#if deck.views_open}
				<a class="btn primary" href={`${deck.talk}presenter/`} rel="external"
					>{words['deck.presenterView']}</a
				>
				<a class="btn secondary" href={deck.talk} rel="external">{words['deck.projectorView']}</a>
			{:else}
				<span class="btn primary off">{words['deck.presenterView']}</span>
				<span class="btn secondary off">{words['deck.projectorView']}</span>
			{/if}
			<a
				class="btn secondary"
				href={`/deck/${encodeURIComponent(deck.slug)}/pdf`}
				rel="external"
				data-sveltekit-reload>{words['deck.pdf']}</a
			>
		</div>
	{:else}
		<p class="muted">{words['deck.notBuiltExplanation']}</p>
	{/if}
{/if}

<style>
	h1 {
		margin: 8px 0 0;
		font-size: 22px;
		overflow-wrap: anywhere;
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
	.standing {
		margin-bottom: 16px;
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
	.btn.off {
		opacity: 0.5;
	}
</style>

<script lang="ts">
	import { resolve } from '$app/paths';
	import { ageInWords } from '#lib/age.ts';
	import { stateWords } from '#lib/deck.ts';
	import { languageTag } from '#lib/session.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);
	const states = $derived(stateWords(words));
	const tag = $derived(languageTag());
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${words.decks_title}`}</title>
</svelte:head>

<h1>{words.decks_title}</h1>
{#if data.list.decks.length > 0}
	<table class="decks">
		<thead>
			<tr>
				<th scope="col">{words.decks_column_state}</th>
				<th scope="col">{words.decks_column_deck}</th>
				<th scope="col">{words.decks_column_changed}</th>
			</tr>
		</thead>
		<tbody>
			{#each data.list.decks as deck (deck.slug)}
				<tr>
					<td><span class="state" data-state={deck.state}><i></i>{states[deck.state]}</span></td>
					<td>
						<a class="as-typed" href={resolve('/(lobby)/deck/[slug]', { slug: deck.slug })}>{deck.title}</a>
						<code>{deck.slug}</code>
					</td>
					<td class="changed">{ageInWords(deck.changed_at, Date.now(), tag)}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{:else}
	<section class="empty">
		<h2>{words.decks_empty_title}</h2>
		<p>
			{#if data.person.role === 'admin'}
				<a href={resolve('/(lobby)/settings/sources')}>{words.decks_empty_explanation}</a>
			{:else}
				{words.decks_empty_explanation}
			{/if}
		</p>
		{#if data.list.source_address}
			<p><code>{data.list.source_address}</code></p>
		{/if}
	</section>
{/if}

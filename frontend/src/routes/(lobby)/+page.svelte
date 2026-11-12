<script lang="ts">
	import { resolve } from '$app/paths';
	import { ageInWords } from '#lib/age.ts';
	import { stateWords } from '#lib/deck.ts';
	import { languageTag } from '#lib/session.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.words);
	const states = $derived(stateWords(words));
	const tag = $derived(languageTag());
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${words['decks.title']}`}</title>
</svelte:head>

<h1>{words['decks.title']}</h1>
{#if data.list.decks.length > 0}
	<table class="decks">
		<thead>
			<tr>
				<th scope="col">{words['decks.columnState']}</th>
				<th scope="col">{words['decks.columnDeck']}</th>
				<th scope="col">{words['decks.columnChanged']}</th>
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
		<h2>{words['decks.emptyTitle']}</h2>
		<p>
			{#if data.person.role === 'admin'}
				<a href={resolve('/(lobby)/settings/sources')}>{words['decks.emptyExplanation']}</a>
			{:else}
				{words['decks.emptyExplanation']}
			{/if}
		</p>
		{#if data.list.source_address}
			<p><code>{data.list.source_address}</code></p>
		{/if}
	</section>
{/if}

<script lang="ts">
	import { ageInWords } from '#lib/age.ts';
	import type { DeckState } from '#lib/api/decks.ts';
	import { languageTag } from '#lib/session.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);
	const stateWords = $derived<Record<DeckState, string>>({
		ready: words.deck_state_ready,
		building: words.deck_state_building,
		failed: words.deck_state_failed,
		'never-built': words.deck_state_never_built
	});
	const tag = $derived(languageTag() ?? words.language_tag);
</script>

<svelte:head>
	<title>{words.decks_title}</title>
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
					<td><span class="state" data-state={deck.state}><i></i>{stateWords[deck.state]}</span></td>
					<td>
						<a class="as-typed" href="/deck/{deck.slug}" rel="external" data-sveltekit-reload>{deck.title}</a>
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
				<a href="/settings/sources" rel="external" data-sveltekit-reload>{words.decks_empty_explanation}</a>
			{:else}
				{words.decks_empty_explanation}
			{/if}
		</p>
		{#if data.list.source_address}
			<p><code>{data.list.source_address}</code></p>
		{/if}
	</section>
{/if}

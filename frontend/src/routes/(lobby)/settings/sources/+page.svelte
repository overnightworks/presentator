<script lang="ts">
	import { invalidate, onNavigate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { Marked } from 'overnightworks-design';
	import { ageInWords } from '#lib/age.ts';
	import ActionButton from '#lib/ActionButton.svelte';
	import StatusLine from '#lib/StatusLine.svelte';
	import { fetchNow, SOURCES_PATH } from '#lib/api/sources.ts';
	import { noticeOf } from '#lib/notice.ts';
	import { languageTag } from '#lib/session.svelte.ts';
	import {
		accessView,
		fetchOutcome,
		removedNotice,
		runFailureWords,
		stateShape,
		stateWords
	} from '#lib/source.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.words);
	const tag = $derived(languageTag());
	const sources = $derived(
		data.sources.map((source) => ({
			...source,
			shape: stateShape(source.state),
			stated: stateWords(source.state, words),
			reasoned: source.reason === null ? null : runFailureWords(source.reason, words),
			access: accessView(source.access, words).tag,
			age: source.fetched_at === null ? '—' : ageInWords(source.fetched_at, Date.now(), tag)
		}))
	);

	// The facts of the removal this list was opened by, taken once here and never
	// again: the toast belongs to this entry and is worded at render.
	let removal = $state(noticeOf().take());
	// Any committing navigation away drops it; a re-read is no navigation.
	onNavigate(() => {
		removal = null;
	});

	// The row whose request runs; every other row is disabled meanwhile.
	let fetching = $state<string | null>(null);

	// How the last fetch ended, beside its source's button; any request of the page clears it.
	let outcome = $state<{ name: string; shape: 'ready' | 'never-built' | 'failed'; text: string } | null>(
		null
	);

	async function fetchSource(name: string): Promise<void> {
		fetching = name;
		outcome = null;
		try {
			try {
				outcome = { name, ...fetchOutcome(await fetchNow(fetch, name), words) };
			} catch (error) {
				outcome = { name, shape: 'failed', text: words['sources.requestFailed'] };
				throw error;
			}
			await invalidate(SOURCES_PATH);
		} finally {
			fetching = null;
		}
	}
</script>

{#snippet code(text: string)}<code>{text}</code>{/snippet}

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${words['sources.title']}`}</title>
</svelte:head>

<div class="head">
	<h1>{words['sources.title']}</h1>
	<a class="btn primary" href={resolve('/(lobby)/settings/sources/new')}>{words['sources.add']}</a>
</div>
{#if removal !== null}
	<output class="toast"
		><span class="state" data-state="ready"><i></i></span>{removedNotice(
			removal.name,
			removal.counts,
			words
		)}</output
	>
{/if}
{#if sources.length > 0}
	<table class="sources">
		<thead>
			<tr>
				<th scope="col">{words['sources.columnState']}</th>
				<th scope="col">{words['sources.columnSource']}</th>
				<th scope="col">{words['sources.columnAccess']}</th>
				<th scope="col">{words['sources.columnFetched']}</th>
				<th scope="col"></th>
			</tr>
		</thead>
		<tbody>
			{#each sources as source (source.name)}
				<tr>
					<td class="state-cell"
						><span class="state" data-state={source.shape}><i></i>{source.stated}</span></td
					>
					<td class="source">
						<a
							class="as-typed"
							href={resolve('/(lobby)/settings/sources/[name]', { name: source.name })}
							>{source.name}</a
						>
						<code>{source.url}</code>
						{#if source.reasoned !== null}<span class="reason">{source.reasoned}</span>{/if}
					</td>
					<td class="access">{#if source.access !== null}{source.access}{/if}</td>
					<td class="fetched">{source.age}</td>
					<td class="row-action">
						<ActionButton
							variant="secondary"
							small
							label={words['sources.fetchNow']}
							busyWord={words['sources.busyFetch']}
							busy={fetching === source.name}
							disabled={fetching !== null}
							onclick={() => fetchSource(source.name)}
						/>
						{#if outcome?.name === source.name}
							<StatusLine shape={outcome.shape} text={outcome.text} />
						{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{:else}
	<section class="empty">
		<h2>{words['sources.emptyTitle']}</h2>
		<p>{words['sources.emptyExplanation']}</p>
		<p><Marked parts={words['sources.emptyNeed']} marked={{ code }} /></p>
		<a class="btn primary" href={resolve('/(lobby)/settings/sources/new')}>{words['sources.add']}</a>
	</section>
{/if}

<style>
	.head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 8px 16px;
		margin: 8px 0 16px;
	}
	h1 {
		margin: 0;
		font-size: 22px;
	}
	.toast {
		margin-bottom: 16px;
	}
	.sources {
		width: 100%;
		border-collapse: collapse;
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.sources th,
	.sources td {
		padding: 12px 16px;
		text-align: left;
		vertical-align: middle;
		border-bottom: 1px solid var(--border);
	}
	.sources th {
		background: var(--bg-deep);
		font-size: 13px;
		font-weight: 400;
		color: var(--text-muted);
	}
	.sources code {
		display: block;
		overflow-wrap: anywhere;
	}
	.source a {
		overflow-wrap: anywhere;
	}
	.reason {
		display: block;
		color: var(--fail);
		font-size: 13px;
	}
	.fetched {
		color: var(--text-muted);
	}
	.row-action {
		text-align: right;
	}
	.row-action :global(.line) {
		display: flex;
		justify-content: flex-end;
		margin-top: 8px;
	}
	@media (max-width: 640px) {
		.sources thead {
			position: absolute;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip-path: inset(50%);
		}
		.sources,
		.sources tbody {
			display: block;
		}
		.sources tr {
			display: grid;
			grid-template-columns: 1fr auto auto;
			align-items: center;
			gap: 4px 12px;
			padding: 12px 16px;
			border-bottom: 1px solid var(--border);
		}
		.sources td {
			padding: 0;
			border: none;
		}
		.sources .source {
			grid-column: 1 / -1;
			grid-row: 2;
		}
		.sources .access {
			grid-column: 3;
			grid-row: 1;
		}
		.sources .fetched {
			grid-column: 2;
			grid-row: 1;
		}
		.sources .row-action {
			grid-column: 1 / -1;
			grid-row: 3;
			text-align: left;
		}
		.row-action :global(.line) {
			justify-content: flex-start;
		}
	}
</style>

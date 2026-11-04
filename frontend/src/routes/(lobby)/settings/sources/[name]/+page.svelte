<script lang="ts">
	import { flushSync } from 'svelte';
	import { invalidate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { ageInWords } from '#lib/age.ts';
	import CopyValue from '#lib/CopyValue.svelte';
	import {
		fetchNow,
		renewAccess,
		renewWebhook,
		sourcePath,
		type WebhookSecretView
	} from '#lib/api/sources.ts';
	import { languageTag } from '#lib/session.svelte.ts';
	import { accessView, runRow, stateShape, stateWords } from '#lib/source.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const source = $derived(data.source);
	const words = $derived(data.session.words);
	const tag = $derived(languageTag());
	const access = $derived(accessView(source.access, words));
	const runs = $derived(
		source.runs.map((run) => ({ ...runRow(run, words), age: ageInWords(run.at, Date.now(), tag) }))
	);

	// The renewed webhook secret lives here and nowhere else.
	let renewed = $state<WebhookSecretView | null>(null);
	let typed = $state('');
	let declined = $state(false);
	let pending = $state(false);

	async function work(attempt: () => Promise<void>): Promise<void> {
		pending = true;
		try {
			await attempt();
		} finally {
			pending = false;
		}
	}

	const reread = (): Promise<void> => invalidate(sourcePath(source.name));

	const fetchSource = (): Promise<void> =>
		work(async () => {
			await fetchNow(fetch, source.name);
			await reread();
		});

	function renewToken(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		return work(async () => {
			declined = (await renewAccess(fetch, source.name, typed)) === 'declined';
			if (!declined) {
				typed = '';
			}
		});
	}

	const renewDeployKey = (): Promise<void> =>
		work(async () => {
			await renewAccess(fetch, source.name, null);
			await reread();
		});

	const renewHook = (): Promise<void> =>
		work(async () => {
			renewed = await renewWebhook(fetch, source.name);
		});

	// A page kept by the back/forward cache is shown again as it was left, so it
	// leaves nothing to show: what was typed and what was shown are gone first.
	function forget(): void {
		renewed = null;
		typed = '';
		declined = false;
		flushSync();
	}
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${source.name}`}</title>
</svelte:head>
<svelte:window onpagehide={forget} />

<a class="back" href="/settings/sources" rel="external" data-sveltekit-reload>{words.sources_back}</a>
<h1 class="as-typed">{source.name}</h1>
<div class="meta">
	<code>{source.url}</code>
	<span class="state" data-state={stateShape(source.state)}
		><i></i>{stateWords(source.state, words)}</span
	>
	<span
		><b>{words.sources_column_fetched}</b
		>{source.fetched_at === null ? '—' : ageInWords(source.fetched_at, Date.now(), tag)}</span
	>
</div>
<div class="actions">
	<button class="btn secondary sm" type="button" disabled={pending} onclick={fetchSource}
		>{words.sources_fetch_now}</button
	>
</div>

<section class="sec">
	<h2>{words.source_access_heading}</h2>
	<div class="ctrl">
		{#if access.tag !== null}<span class="tag">{access.tag}</span>{/if}
		{#if access.control === 'token'}
			<samp class="dots">{words.source_secret_dots}</samp>
		{/if}
		{#if access.control !== 'none'}
			{@const hint = access.control === 'token' ? words.source_access_renew_info : words.source_deploy_key_info}
			<button class="info" type="button" popovertarget="access-hint" aria-label={hint}>
				<svg viewBox="0 0 24 24" aria-hidden="true">
					<circle cx="12" cy="12" r="9" />
					<path d="M12 11v5M12 7.5v.01" />
				</svg>
			</button>
			<span id="access-hint" class="hint" popover="auto">{hint}</span>
		{/if}
	</div>
	{#if access.control === 'deploy-key'}
		{#if source.public_key !== null}
			<div class="fld">
				<span class="lb" id="public-key-label">{words.source_public_key}</span>
				<CopyValue
					value={source.public_key}
					label={words.source_copy}
					labelledby="public-key-label"
				/>
			</div>
		{/if}
		<div class="actions">
			<button class="btn secondary sm" type="button" disabled={pending} onclick={renewDeployKey}
				>{words.source_deploy_key_renew}</button
			>
		</div>
	{:else if access.control === 'token'}
		<form class="form" onsubmit={renewToken}>
			<div class="fld">
				<label for="renew-secret">{words.source_secret}</label>
				<input
					class="control"
					id="renew-secret"
					name="secret"
					type="password"
					autocomplete="off"
					required
					bind:value={typed}
				/>
			</div>
			{#if declined}
				<div class="banner" data-banner="fail" role="alert">
					<span class="state" data-state="failed"><i></i></span>
					<p>{words.source_refused_secret}</p>
				</div>
			{/if}
			<div class="actions">
				<button class="btn secondary sm" type="submit" disabled={pending}
					>{words.source_renew}</button
				>
			</div>
		</form>
	{/if}
</section>

<section class="sec">
	<h2>{words.source_webhook_heading}</h2>
	<CopyValue value={source.hook_url} label={words.source_copy} labelledby="hook-address-label" />
	{#if renewed === null}
		<div class="ctrl"><samp class="dots">{words.source_secret_dots}</samp></div>
	{:else}
		<div class="fld">
			<span class="lb" id="hook-secret-label"
				>{words.source_webhook_secret}<span class="once">{words.source_webhook_once}</span></span
			>
			<CopyValue
				value={renewed.webhook_secret}
				label={words.source_copy}
				labelledby="hook-secret-label"
			/>
		</div>
	{/if}
	<div class="actions">
		<button class="btn secondary sm" type="button" disabled={pending} onclick={renewHook}
			>{words.source_renew}</button
		>
	</div>
</section>

{#if runs.length > 0}
	<section class="sec">
		<h2>{words.source_recent_runs}</h2>
		<table class="runs">
			<thead>
				<tr>
					<th scope="col">{words.source_run_column_state}</th>
					<th scope="col">{words.source_run_column_fetched}</th>
					<th scope="col">{words.source_run_column_detail}</th>
				</tr>
			</thead>
			<tbody>
				{#each runs as run, index (index)}
					<tr>
						<td><span class="state" data-state={run.shape}><i></i>{run.state}</span></td>
						<td class="age">{run.age}</td>
						<td>
							{#if run.detail !== null}
								{#if run.detail.kind === 'commit'}
									<samp>{run.detail.text}</samp>
								{:else}
									{run.detail.text}
								{/if}
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>
{/if}

<section class="sec">
	<h2>{words.source_decks}</h2>
	<div class="chips">
		{#each source.decks as deck (deck.slug)}
			<a class="chip" href={resolve('/(lobby)/deck/[slug]', { slug: deck.slug })}>
				<b class="as-typed">{deck.title}</b>
				<code>{deck.slug}</code>
			</a>
		{/each}
	</div>
</section>

<section class="sec">
	<h2>{words.source_reverse}</h2>
	<p class="later">
		<span>{words.source_push_inbox}</span>
		<span class="tag">{words.source_later}</span>
	</p>
</section>

<!-- Removal stays the Jinja confirm until the removal dialog replaces this form. -->
<section class="sec">
	<h2>{words.source_remove_heading}</h2>
	<p class="muted">{words.source_remove_hint}</p>
	<form
		class="actions"
		method="post"
		action={`/settings/sources/${encodeURIComponent(source.name)}/remove`}
	>
		<button class="btn secondary sm" type="submit" disabled={pending}
			>{words.source_remove_button}</button
		>
	</form>
</section>

<style>
	h1 {
		margin: 8px 0 12px;
		font-size: 22px;
		overflow-wrap: anywhere;
	}
	.form {
		display: flex;
		flex-direction: column;
		gap: 14px;
		max-width: 520px;
	}
	.dots {
		letter-spacing: 0.1em;
	}
	.banner {
		margin-bottom: 0;
	}
	.banner p {
		margin: 0;
	}
</style>

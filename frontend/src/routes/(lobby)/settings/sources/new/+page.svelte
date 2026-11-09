<script lang="ts">
	import { flushSync, untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Refused } from '#lib/api/client.ts';
	import {
		type AccessKind,
		checkConnection,
		createSource,
		type CreatedSourceView,
		openKeyDraft,
		readAccess,
		type SourceProbe
	} from '#lib/api/sources.ts';
	import CopyValue from '#lib/CopyValue.svelte';
	import { navigation } from '#lib/navigation.ts';
	import { type CheckBanner, checkBanner, refusalWords } from '#lib/source.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);

	let name = $state('');
	let url = $state('');
	let secret = $state('');
	let draft = $state(untrack(() => data.draft));
	// What the server said the typed address needs. Only the latest lookup may write it.
	type Lookup =
		| { state: 'idle' }
		| { state: 'pending' }
		| { state: 'failed' }
		| { state: 'answered'; url: string; access: AccessKind | null };
	let lookup = $state<Lookup>({ state: 'idle' });
	let generation = 0;
	// Undefined: no usable answer for the current text. Null: answered, no access.
	const known = $derived(
		lookup.state === 'answered' && lookup.url === url ? lookup.access : undefined
	);
	// What the last check found, and the proof a create needs from it.
	let banner = $state<CheckBanner | null>(null);
	let fingerprint = $state<string | null>(null);
	let refusal = $state<string | null>(null);
	let pending = $state(false);
	// The only place the webhook secret lives: this page's memory.
	let created = $state<CreatedSourceView | null>(null);

	// Back to the entry a create replaced loads this route under the source's
	// address; the source's own page is what that address shows. Checked once,
	// here: a reactive check would fire right after a create and destroy the
	// one-time display. This page makes no other shallow navigation.
	if (page.shallow !== null) {
		void navigation.goto(page.shallow.url.href, { replace: true });
	}

	const probe = (): SourceProbe => ({
		url,
		secret: known === 'https-token' && secret !== '' ? secret : null,
		key_draft_id: draft.id
	});

	async function lookUp(address: string): Promise<void> {
		generation += 1;
		const mine = generation;
		if (address.trim() === '') {
			lookup = { state: 'idle' };
			return;
		}
		lookup = { state: 'pending' };
		try {
			const access = await readAccess(fetch, address);
			if (mine !== generation) {
				return;
			}
			lookup = { state: 'answered', url: address, access };
			if (access !== 'https-token') {
				secret = '';
			}
		} catch {
			if (mine === generation) {
				lookup = { state: 'failed' };
			}
		}
	}

	// What a check found was for the address it read; an edit leaves nothing of it.
	function editAddress(address: string): Promise<void> {
		banner = null;
		fingerprint = null;
		refusal = null;
		return lookUp(address);
	}

	// A refusal is the person's to fix; anything else is a failure to show loudly.
	function refused(failure: unknown): void {
		if (!(failure instanceof Refused)) {
			throw failure;
		}
		refusal = refusalWords(failure.reason, words);
	}

	// The draft may have been used or replaced since it was shown.
	async function reloadDraft(): Promise<void> {
		draft = await openKeyDraft(fetch, window.location);
	}

	async function work(attempt: () => Promise<void>): Promise<void> {
		pending = true;
		refusal = null;
		try {
			try {
				await attempt();
				if (created !== null) {
					return;
				}
			} catch (failure) {
				refused(failure);
			}
			await reloadDraft();
		} finally {
			pending = false;
		}
	}

	const check = (): Promise<void> =>
		work(async () => {
			banner = null;
			fingerprint = null;
			const found = await checkConnection(fetch, probe());
			banner = checkBanner(found, words);
			fingerprint = found.fingerprint;
		});

	function create(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		return work(async () => {
			// Create is disabled until a check answered with a fingerprint.
			created = await createSource(fetch, {
				...probe(),
				name,
				folder: '',
				fingerprint: fingerprint as string
			});
			secret = '';
			banner = null;
			fingerprint = null;
			// The bar takes the source's address with no new history entry and no
			// state, so Back and a reload meet the source's page, never the secret.
			await navigation.goto(resolve('/(lobby)/settings/sources/[name]', { name: created.name }), {
				shallow: true,
				replace: true,
				state: {}
			});
		});
	}

	// A page kept by the back/forward cache is shown again as it was left, so it
	// leaves nothing to show: the form is empty before the page is frozen.
	function forget(): void {
		created = null;
		name = '';
		url = '';
		generation += 1;
		lookup = { state: 'idle' };
		secret = '';
		banner = null;
		fingerprint = null;
		refusal = null;
		flushSync();
	}
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${words.sources_add}`}</title>
</svelte:head>
<svelte:window onpagehide={forget} />

<a class="back" href={resolve('/(lobby)/settings/sources')}>{words.sources_back}</a>
{#if created === null}
	<h1>{words.sources_add}</h1>
	<form class="form" onsubmit={create}>
		<div class="fld">
			<label for="source-name">{words.source_name}</label>
			<input
				class="control"
				id="source-name"
				name="name"
				autocomplete="off"
				required
				bind:value={name}
			/>
		</div>
		<div class="fld">
			<label for="source-url">{words.source_url}</label>
			<input
				class="control"
				id="source-url"
				name="url"
				autocomplete="off"
				spellcheck="false"
				required
				bind:value={url}
				oninput={(event) => editAddress(event.currentTarget.value)}
			/>
		</div>
		{#if lookup.state === 'pending'}
			<p class="muted" role="status">{words.source_kind_pending}</p>
		{:else if lookup.state === 'failed'}
			<div class="banner" data-banner="fail" role="alert">
				<span class="state" data-state="failed"><i></i></span>
				<div>
					<p>{words.source_kind_failed}</p>
					<button class="btn secondary" type="button" onclick={() => lookUp(url)}
						>{words.source_kind_retry}</button
					>
				</div>
			</div>
		{/if}
		{#if known === 'https-token'}
			<div class="fld">
				<span class="lb">
					<label for="source-secret">{words.source_secret}</label>
					<button
						class="info"
						type="button"
						popovertarget="secret-hint"
						aria-label={words.source_secret_info}
					>
						<svg viewBox="0 0 24 24" aria-hidden="true">
							<circle cx="12" cy="12" r="9" />
							<path d="M12 11v5M12 7.5v.01" />
						</svg>
					</button>
				</span>
				<input
					class="control"
					id="source-secret"
					name="secret"
					type="password"
					autocomplete="new-password"
					bind:value={secret}
				/>
				<span id="secret-hint" class="hint" popover="auto">{words.source_secret_info}</span>
			</div>
		{:else if known === 'ssh-deploy-key'}
			<div class="fld">
				<h2 class="lb" id="deploy-key-label">{words.source_deploy_key_heading}</h2>
				<span class="muted">{words.source_deploy_key_hint}</span>
				<CopyValue
					value={draft.public_key}
					label={words.source_copy}
					labelledby="deploy-key-label"
				/>
			</div>
		{/if}
		<p class="muted">{words.source_webhook_later}</p>
		{#if refusal !== null}
			<div class="banner" data-banner="fail" role="alert">
				<span class="state" data-state="failed"><i></i></span>
				<p>{refusal}</p>
			</div>
		{/if}
		{#if banner !== null}
			<div class="banner" data-banner={banner.shape} role="status">
				<span class="state" data-state={banner.shape === 'ok' ? 'ready' : 'failed'}><i></i></span>
				<div>
					<p><span class="state-word">{banner.state}</span>{` ${banner.message}`}</p>
					{#if banner.detail !== null}
						<p><samp>{banner.detail}</samp></p>
					{/if}
				</div>
			</div>
		{/if}
		<div class="actions">
			<button
				class="btn secondary"
				type="button"
				disabled={pending || known === undefined}
				onclick={check}
				>{words.source_check}</button
			>
			<button class="btn primary" type="submit" disabled={pending || fingerprint === null}
				>{words.source_create}</button
			>
		</div>
	</form>
{:else}
	<!-- Template literals: a bare interpolation in a text compiles to a `?? ''` branch no page reaches. -->
	<h1><span class="as-typed">{`${created.name}`}</span>{` ${words.source_created}`}</h1>
	<section class="sec">
		<h2>{words.source_webhook}</h2>
		<div class="fld">
			<span class="lb" id="hook-address-label">{words.source_webhook_address}</span>
			<CopyValue
				value={created.hook_url}
				label={words.source_copy}
				labelledby="hook-address-label"
			/>
		</div>
		<div class="fld">
			<span class="lb" id="hook-secret-label"
				>{words.source_webhook_secret}<span class="once">{words.source_webhook_once}</span></span
			>
			<CopyValue
				value={created.webhook_secret}
				label={words.source_copy}
				labelledby="hook-secret-label"
			/>
		</div>
		<p class="muted">{words.source_webhook_payload}</p>
	</section>
	<div class="actions">
		<a
			class="btn primary"
			href={`/settings/sources/${encodeURIComponent(created.name)}`}
			rel="external"
			data-sveltekit-reload>{words.source_created_done}</a
		>
	</div>
	<output class="toast"
		><span class="state" data-state="ready"><i></i></span>{words.source_created_toast}</output
	>
{/if}

<style>
	h1 {
		margin: 8px 0 16px;
		font-size: 22px;
		overflow-wrap: anywhere;
	}
	.form {
		display: flex;
		flex-direction: column;
		gap: 14px;
		max-width: 520px;
	}
	h2.lb {
		margin: 0;
		font-weight: 400;
	}
	.banner {
		margin-bottom: 0;
	}
	.banner .state {
		margin-top: 3px;
	}
	.banner p {
		margin: 0 0 4px;
		overflow-wrap: anywhere;
	}
	.state-word {
		font-weight: 600;
	}
	.banner samp {
		font-family: ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace;
		font-size: 0.85em;
		white-space: pre-wrap;
	}
</style>

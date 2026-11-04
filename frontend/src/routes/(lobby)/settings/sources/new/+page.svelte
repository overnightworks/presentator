<script lang="ts">
	import { flushSync, untrack } from 'svelte';
	import { Refused } from '#lib/api/client.ts';
	import {
		type AccessKind,
		checkConnection,
		createSource,
		type CreatedSourceView,
		openKeyDraft,
		type SourceProbe
	} from '#lib/api/sources.ts';
	import CopyValue from '#lib/CopyValue.svelte';
	import { type CheckBanner, checkBanner, refusalWords } from '#lib/source.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);
	const accesses = $derived<{ kind: AccessKind; label: string }[]>([
		{ kind: 'ssh-deploy-key', label: words.source_access_ssh },
		{ kind: 'https-token', label: words.source_access_https },
		{ kind: 'local-folder', label: words.source_access_file }
	]);

	let name = $state('');
	let url = $state('');
	let access = $state<AccessKind>('https-token');
	let secret = $state('');
	let draft = $state(untrack(() => data.draft));
	// What the last check found, and the proof a create needs from it.
	let banner = $state<CheckBanner | null>(null);
	let fingerprint = $state<string | null>(null);
	let refusal = $state<string | null>(null);
	let pending = $state(false);
	// The only place the webhook secret lives: this page's memory.
	let created = $state<CreatedSourceView | null>(null);

	const probe = (): SourceProbe => ({
		url,
		access,
		secret: secret === '' ? null : secret,
		key_draft_id: draft.id
	});

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
				fingerprint: fingerprint as string
			});
			secret = '';
			banner = null;
			fingerprint = null;
		});
	}

	// A page kept by the back/forward cache is shown again as it was left, so it
	// leaves nothing to show: the secret is gone before the page is frozen.
	function forget(): void {
		created = null;
		secret = '';
		flushSync();
	}
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${words.sources_add}`}</title>
</svelte:head>
<svelte:window onpagehide={forget} />

<a class="back" href="/settings/sources" rel="external" data-sveltekit-reload>{words.sources_back}</a>
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
			/>
		</div>
		<fieldset class="fld access">
			<legend class="lb">{words.source_access}</legend>
			<div class="seg">
				{#each accesses as option (option.kind)}
					<label>
						<input type="radio" name="access" {...{ value: option.kind }} bind:group={access} />
						<span>{option.label}</span>
					</label>
				{/each}
			</div>
		</fieldset>
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
				autocomplete="off"
				bind:value={secret}
			/>
			<span id="secret-hint" class="hint" popover="auto">{words.source_secret_info}</span>
		</div>
		<div class="fld">
			<h2 class="lb" id="deploy-key-label">{words.source_deploy_key_heading}</h2>
			<span class="muted">{words.source_deploy_key_hint}</span>
			<CopyValue
				value={draft.public_key}
				label={words.source_copy}
				labelledby="deploy-key-label"
			/>
		</div>
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
					<p>{banner.message}</p>
					{#if banner.detail !== null}
						<p><samp>{banner.detail}</samp></p>
					{/if}
				</div>
			</div>
		{/if}
		<div class="actions">
			<button class="btn secondary" type="button" disabled={pending} onclick={check}
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
	.form,
	.sec {
		display: flex;
		flex-direction: column;
		gap: 14px;
		max-width: 520px;
	}
	.sec {
		gap: 10px;
	}
	.sec h2 {
		margin: 0;
		font-size: 13px;
		font-weight: 400;
		letter-spacing: 0.1em;
		color: var(--text-muted);
	}
	.muted {
		margin: 0;
		font-size: 14px;
		color: var(--text-muted);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin: 16px 0;
	}
	.access {
		margin: 0;
		padding: 0;
		border: none;
	}
	.access legend {
		padding: 0;
	}
	h2.lb {
		margin: 0;
		font-weight: 400;
	}
	.seg {
		display: inline-flex;
		flex-wrap: wrap;
		align-self: flex-start;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--surface);
	}
	.seg label {
		position: relative;
		display: inline-flex;
		align-items: center;
		min-height: 36px;
		padding: 0 14px;
		color: var(--text-muted);
		font-size: 14px;
		cursor: pointer;
	}
	.seg label + label {
		border-left: 1px solid var(--border);
	}
	.seg label:has(input:checked) {
		background: color-mix(in srgb, var(--accent) 12%, var(--surface));
		color: var(--text);
		font-weight: 600;
	}
	.seg label:has(input:focus-visible) {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}
	.seg input {
		position: absolute;
		opacity: 0;
		pointer-events: none;
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
	.banner samp {
		font-family: ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace;
		font-size: 0.85em;
		white-space: pre-wrap;
	}
</style>

<script lang="ts">
	import { flushSync, onDestroy } from 'svelte';
	import { goto, invalidate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { navigating } from '$app/state';
	import { ageInWords } from '#lib/age.ts';
	import ActionButton from '#lib/ActionButton.svelte';
	import CopyValue from '#lib/CopyValue.svelte';
	import StatusLine from '#lib/StatusLine.svelte';
	import type { RemovalCounts } from '#lib/api/client.ts';
	import {
		fetchNow,
		previewRemoval,
		removeSource,
		renewAccess,
		renewWebhook,
		sourcePath,
		type WebhookSecretView
	} from '#lib/api/sources.ts';
	import { noticeOf } from '#lib/notice.ts';
	import { languageTag } from '#lib/session.svelte.ts';
	import {
		accessView,
		fetchOutcome,
		removalBody,
		removalTitle,
		runRow,
		stateShape,
		stateWords
	} from '#lib/source.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const source = $derived(data.source);
	const words = $derived(data.words);
	const tag = $derived(languageTag());
	const access = $derived(accessView(source.access, words));
	const runs = $derived(
		source.runs.map((run) => ({ ...runRow(run, words), age: ageInWords(run.at, Date.now(), tag) }))
	);

	const title = $derived(removalTitle(words));
	// A 204 is acted on only while this page is mounted and no other navigation is under way.
	const notice = noticeOf();
	let destroyed = false;
	onDestroy(() => {
		destroyed = true;
	});

	// The renewed webhook secret lives here and nowhere else.
	let renewed = $state<WebhookSecretView | null>(null);
	let typed = $state('');
	let declined = $state(false);
	// Which request runs; every button of the page is disabled meanwhile.
	let running = $state<Request | null>(null);
	// How the last request ended and which one it was; any request of the page clears it as it starts.
	let outcome = $state<(ReturnType<typeof fetchOutcome> & { request: Request }) | null>(null);
	const pending = $derived(running !== null);
	// The dialog is mounted with the page, so a click handler always finds it.
	let dialog = $state() as HTMLDialogElement;
	let opener = $state() as HTMLButtonElement;
	// What the open dialog shows and sends; null while it is closed.
	let counts = $state<RemovalCounts | null>(null);
	let refused = $state(false);

	type Request = 'fetch' | 'renewKey' | 'renewToken' | 'renewHook' | 'count' | 'remove';

	// A request that ends well may name its outcome; one that throws ends as a failure and rethrows.
	async function work(
		request: Request,
		attempt: () => Promise<ReturnType<typeof fetchOutcome> | null>
	): Promise<void> {
		running = request;
		outcome = null;
		try {
			const ended = await attempt();
			outcome = ended && { ...ended, request };
		} catch (error) {
			outcome = { request, shape: 'failed', text: words['source.requestFailed'] };
			throw error;
		} finally {
			running = null;
		}
	}

	const reread = (): Promise<void> => invalidate(sourcePath(source.name));

	const fetchSource = (): Promise<void> =>
		work('fetch', async () => {
			const ended = fetchOutcome(await fetchNow(fetch, source.name), words);
			await reread();
			return ended;
		});

	function renewToken(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		return work('renewToken', async () => {
			declined = (await renewAccess(fetch, source.name, typed)) === 'declined';
			if (!declined) {
				typed = '';
			}
			return declined ? null : { shape: 'ready', text: words['source.renewedSecret'] };
		});
	}

	const renewDeployKey = (): Promise<void> =>
		work('renewKey', async () => {
			await renewAccess(fetch, source.name, null);
			await reread();
			return { shape: 'ready', text: words['source.renewedDeployKey'] };
		});

	const renewHook = (): Promise<void> =>
		work('renewHook', async () => {
			renewed = await renewWebhook(fetch, source.name);
			return { shape: 'ready', text: words['source.renewedWebhook'] };
		});

	// The dialog opens once the buttons are enabled again, from the control that
	// asked, so closing it returns focus there and Cancel takes it on opening.
	async function askRemoval(): Promise<void> {
		await work('count', async () => {
			counts = await previewRemoval(fetch, source.name);
			refused = false;
			return null;
		});
		flushSync();
		opener.focus();
		dialog.showModal();
	}

	const confirmRemoval = (): Promise<void> =>
		work('remove', async () => {
			const confirmed = counts as RemovalCounts;
			refused = false;
			const answer = await removeSource(fetch, source.name, confirmed);
			if (answer === 'refused') {
				refused = true;
			} else if (answer !== 'removed') {
				counts = answer.stale;
			} else if (!destroyed && navigating.type === null) {
				notice.show({ name: source.name, counts: confirmed });
				await goto(resolve('/(lobby)/settings/sources'), { replace: true });
				// An overtaking navigation leaves the notice untaken; the list took it otherwise.
				notice.take();
			}
			return null;
		});

	// A page kept by the back/forward cache is shown again as it was left, so it
	// leaves nothing to show: what was typed, shown and asked is gone first.
	function forget(): void {
		renewed = null;
		outcome = null;
		typed = '';
		declined = false;
		counts = null;
		refused = false;
		dialog.close();
		flushSync();
	}
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${source.name}`}</title>
</svelte:head>
<svelte:window onpagehide={forget} />

<a class="back" href={resolve('/(lobby)/settings/sources')}>{words['source.back']}</a>
<h1 class="as-typed">{source.name}</h1>
<div class="meta">
	<code>{source.url}</code>
	{#if source.folder !== ''}
		<span><b>{words['source.folder']}</b>{source.folder}</span>
	{/if}
	<span class="state" data-state={stateShape(source.state)}
		><i></i>{stateWords(source.state, words)}</span
	>
	<span
		><b>{words['source.columnFetched']}</b
		>{source.fetched_at === null ? '—' : ageInWords(source.fetched_at, Date.now(), tag)}</span
	>
</div>
<div class="actions">
	<ActionButton
		variant="secondary"
		small
		label={words['source.fetchNow']}
		busyWord={words['source.busyFetch']}
		busy={running === 'fetch'}
		disabled={pending}
		onclick={fetchSource}
	/>
	{#if outcome?.request === 'fetch'}<StatusLine shape={outcome.shape} text={outcome.text} />{/if}
</div>

<section class="sec">
	<h2>{words['source.accessHeading']}</h2>
	<div class="ctrl">
		{#if access.tag !== null}<span class="tag">{access.tag}</span>{/if}
		{#if access.control === 'token'}
			<samp class="dots">{words['source.secretMask']}</samp>
		{/if}
		{#if access.control !== 'none'}
			{@const hint = access.control === 'token' ? words['source.accessRenewInfo'] : words['source.deployKeyInfo']}
			<button class="info" type="button" popovertarget="access-hint" aria-label={hint} disabled={pending}>
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
				<span class="lb" id="public-key-label">{words['source.publicKey']}</span>
				<CopyValue
					value={source.public_key}
					label={words['source.copy']}
					labelledby="public-key-label"
					disabled={pending}
				/>
			</div>
		{/if}
		<div class="actions">
			<ActionButton
				variant="secondary"
				small
				label={words['source.deployKeyRenew']}
				busyWord={words['source.busyRenew']}
				busy={running === 'renewKey'}
				disabled={pending}
				onclick={renewDeployKey}
			/>
			{#if outcome?.request === 'renewKey'}<StatusLine shape={outcome.shape} text={outcome.text} />{/if}
		</div>
	{:else if access.control === 'token'}
		<form class="form" onsubmit={renewToken}>
			<div class="fld">
				<label for="renew-secret">{words['source.secret']}</label>
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
					<p>{words['source.refusedSecret']}</p>
				</div>
			{/if}
			<div class="actions">
				<ActionButton
					variant="secondary"
					small
					type="submit"
					label={words['source.renew']}
					busyWord={words['source.busyRenew']}
					busy={running === 'renewToken'}
					disabled={pending}
				/>
				{#if outcome?.request === 'renewToken'}<StatusLine shape={outcome.shape} text={outcome.text} />{/if}
			</div>
		</form>
	{/if}
</section>

<section class="sec">
	<h2>{words['source.webhookHeading']}</h2>
	<div class="fld">
		<span class="lb" id="hook-address-label">{words['source.webhookAddress']}</span>
		<CopyValue
			value={source.hook_url}
			label={words['source.copy']}
			labelledby="hook-address-label"
			disabled={pending}
		/>
	</div>
	{#if renewed === null}
		<div class="ctrl"><samp class="dots">{words['source.secretMask']}</samp></div>
	{:else}
		<div class="fld">
			<span class="lb" id="hook-secret-label"
				>{words['source.webhookSecret']}<span class="once">{words['source.webhookOnce']}</span></span
			>
			<CopyValue
				value={renewed.webhook_secret}
				label={words['source.copy']}
				labelledby="hook-secret-label"
				disabled={pending}
			/>
		</div>
	{/if}
	<div class="actions">
		<ActionButton
			variant="secondary"
			small
			label={words['source.renew']}
			busyWord={words['source.busyRenew']}
			busy={running === 'renewHook'}
			disabled={pending}
			onclick={renewHook}
		/>
		{#if outcome?.request === 'renewHook'}<StatusLine shape={outcome.shape} text={outcome.text} />{/if}
	</div>
</section>

{#if runs.length > 0}
	<section class="sec">
		<h2>{words['source.recentRuns']}</h2>
		<table class="runs">
			<thead>
				<tr>
					<th scope="col">{words['source.runColumnState']}</th>
					<th scope="col">{words['source.runColumnFetched']}</th>
					<th scope="col">{words['source.runColumnDetail']}</th>
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

{#if source.decks.length > 0}
	<section class="sec">
		<h2>{words['source.decks']}</h2>
		<div class="chips">
			{#each source.decks as deck (deck.slug)}
				<a class="chip" href={resolve('/(lobby)/deck/[slug]', { slug: deck.slug })}>
					<b class="as-typed">{deck.title}</b>
					<code>{deck.slug}</code>
				</a>
			{/each}
		</div>
	</section>
{/if}

<section class="sec">
	<h2>{words['source.reverse']}</h2>
	<p class="later">
		<span>{words['source.pushInbox']}</span>
		<span class="tag">{words['source.later']}</span>
	</p>
</section>

<section class="sec">
	<h2>{words['source.removeHeading']}</h2>
	<p class="muted">{words['source.removeHint']}</p>
	<div class="actions">
		<ActionButton
			variant="secondary"
			small
			bind:element={opener}
			label={words['source.removeButton']}
			busyWord={words['source.busyCount']}
			busy={running === 'count'}
			disabled={pending}
			onclick={askRemoval}
		/>
		{#if outcome?.request === 'count' || outcome?.request === 'remove'}<StatusLine
				shape={outcome.shape}
				text={outcome.text}
			/>{/if}
	</div>
</section>

<dialog
	bind:this={dialog}
	aria-labelledby="remove-title"
	// Closed under a pending DELETE, the dialog would hide the answer to it.
	closedby={pending ? 'none' : 'closerequest'}
	onclose={() => {
		counts = null;
		refused = false;
	}}
>
	{#if counts !== null}
		<h2 id="remove-title">{title.before}<span class="as-typed">{source.name}</span>{title.after}</h2>
		{#if refused}
			<div class="banner" data-banner="fail" role="alert">
				<span class="state" data-state="failed"><i></i></span>
				<p>{words['source.removeRefused']}</p>
			</div>
		{/if}
		<p>{removalBody(counts, words)}</p>
		<div class="actions">
			<button
				class="btn secondary"
				type="button"
				disabled={pending}
				onclick={() => dialog.close()}>{words['source.removeCancel']}</button
			>
			<ActionButton
				variant="primary"
				label={words['source.removeConfirm']}
				busyWord={words['source.busyRemove']}
				busy={running === 'remove'}
				disabled={pending}
				onclick={confirmRemoval}
			/>
		</div>
	{/if}
</dialog>

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
	dialog {
		box-sizing: border-box;
		max-width: min(480px, calc(100vw - 32px));
		padding: 20px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--surface);
		color: var(--text);
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 50%);
	}
	dialog h2 {
		margin: 0 0 12px;
		font-size: 18px;
		overflow-wrap: anywhere;
	}
	dialog p {
		margin: 0 0 16px;
	}
</style>

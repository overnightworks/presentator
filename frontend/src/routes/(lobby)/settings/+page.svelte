<script lang="ts">
	import { untrack } from 'svelte';
	import { invalidate } from '$app/navigation';
	import { SESSION_PATH } from '#lib/api/session.ts';
	import { writeSettings } from '#lib/api/settings.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.words);

	// What is typed, from the stored values at first. The session re-read after a
	// save makes `data` new again, and that must not put the old values back.
	let name = $state(untrack(() => data.settings.name));
	let language = $state(untrack(() => data.settings.language_tag));
	let saving = $state(false);

	// The language belongs to the server, so the repaint reads the session again.
	async function save(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		saving = true;
		try {
			const stored = await writeSettings(fetch, { name, language_tag: language });
			name = stored.name;
			language = stored.language_tag;
			await invalidate(SESSION_PATH);
		} finally {
			saving = false;
		}
	}
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${words['settings.title']}`}</title>
</svelte:head>

<h1>{words['settings.title']}</h1>
<form class="form" onsubmit={save}>
	<div class="fld">
		<label for="instance-name">{words['settings.instanceName']}</label>
		<input class="control" id="instance-name" name="name" autocomplete="off" bind:value={name} />
	</div>
	<div class="fld">
		<span class="lb">
			<label for="default-language">{words['settings.defaultLanguage']}</label>
			<button
				class="info"
				type="button"
				popovertarget="language-hint"
				aria-label={words['settings.languageHint']}
			>
				<svg viewBox="0 0 24 24" aria-hidden="true">
					<circle cx="12" cy="12" r="9" />
					<path d="M12 11v5M12 7.5v.01" />
				</svg>
			</button>
		</span>
		<!-- Spread values: an option's own `value` compiles to branches no page reaches. -->
		<select class="control" id="default-language" name="language" bind:value={language}>
			{#each data.settings.languages as tag (tag)}
				<option {...{ value: tag }}>{`${new Intl.DisplayNames(tag, { type: 'language' }).of(tag)}`}</option>
			{/each}
		</select>
		<span id="language-hint" class="hint" popover="auto">{words['settings.languageHint']}</span>
	</div>
	<div class="actions">
		<button class="btn primary" type="submit" disabled={saving}>{words['settings.save']}</button>
	</div>
</form>

<style>
	.form {
		display: flex;
		flex-direction: column;
		gap: 14px;
		max-width: 520px;
	}
	.actions {
		display: flex;
		gap: 8px;
	}
</style>

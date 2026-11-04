<script lang="ts">
	import { untrack } from 'svelte';
	import { invalidate } from '$app/navigation';
	import type { ThemeChoice } from '#lib/api/account.ts';
	import { SESSION_PATH } from '#lib/api/session.ts';
	import { writeSettings } from '#lib/api/settings.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);
	const themes = $derived<{ value: ThemeChoice; label: string }[]>([
		{ value: 'follow-system', label: words.theme_follow_system },
		{ value: 'light', label: words.theme_light },
		{ value: 'dark', label: words.theme_dark }
	]);

	// What is typed, from the stored values at first. The session re-read after a
	// save makes `data` new again, and that must not put the old values back.
	let name = $state(untrack(() => data.settings.name));
	let language = $state(untrack(() => data.settings.language_tag));
	let theme = $state(untrack(() => data.settings.theme));
	let saving = $state(false);

	// The effective look belongs to the server (a person's own choice outranks
	// the default), so the repaint reads the session again instead of setting a theme.
	async function save(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		saving = true;
		try {
			const stored = await writeSettings(fetch, { name, language_tag: language, theme });
			name = stored.name;
			language = stored.language_tag;
			theme = stored.theme;
			await invalidate(SESSION_PATH);
		} finally {
			saving = false;
		}
	}
</script>

<svelte:head>
	<!-- A template literal: a bare interpolation compiles to a `?? ''` branch no page reaches. -->
	<title>{`${words.settings_title}`}</title>
</svelte:head>

<h1>{words.settings_title}</h1>
<form class="form" onsubmit={save}>
	<div class="fld">
		<label for="instance-name">{words.settings_instance_name}</label>
		<input class="control" id="instance-name" name="name" autocomplete="off" bind:value={name} />
	</div>
	<div class="fld">
		<span class="lb">
			<label for="default-language">{words.settings_default_language}</label>
			<button
				class="info"
				type="button"
				popovertarget="language-hint"
				aria-label={words.settings_language_hint}
			>
				<svg viewBox="0 0 24 24" aria-hidden="true">
					<circle cx="12" cy="12" r="9" />
					<path d="M12 11v5M12 7.5v.01" />
				</svg>
			</button>
		</span>
		<!-- Spread values: an option's own `value` compiles to branches no page reaches. -->
		<select class="control" id="default-language" name="language" bind:value={language}>
			{#each data.settings.languages as option (option.tag)}
				<option {...{ value: option.tag }}>{option.name}</option>
			{/each}
		</select>
		<span id="language-hint" class="hint" popover="auto">{words.settings_language_hint}</span>
	</div>
	<div class="fld">
		<label for="default-theme">{words.settings_default_theme}</label>
		<select class="control" id="default-theme" name="theme" bind:value={theme}>
			{#each themes as option (option.value)}
				<option {...{ value: option.value }}>{option.label}</option>
			{/each}
		</select>
	</div>
	<div class="actions">
		<button class="btn primary" type="submit" disabled={saving}>{words.settings_save}</button>
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

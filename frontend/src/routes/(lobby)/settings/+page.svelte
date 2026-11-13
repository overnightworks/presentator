<script lang="ts">
	import { untrack } from 'svelte';
	import { writeSettings } from '#lib/api/settings.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.words);

	// What is typed, from the stored value at first.
	let name = $state(untrack(() => data.settings.name));
	let saving = $state(false);

	async function save(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		saving = true;
		try {
			const stored = await writeSettings(fetch, { name });
			name = stored.name;
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

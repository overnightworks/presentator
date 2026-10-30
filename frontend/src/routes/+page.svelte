<script>
	let presses = $state(0);
	let name = $state('');
	let colour = $state('teal');
	let submitted = $state('');
	let theme = $state(document.documentElement.dataset.theme ?? 'follow-system');
	/** @type {HTMLDialogElement | undefined} */
	let dialog = $state();

	/** @param {SubmitEvent} event */
	function submit(event) {
		event.preventDefault();
		submitted = `${name} / ${colour}`;
	}

	/** @param {string} choice */
	function chooseTheme(choice) {
		theme = choice;
		document.documentElement.dataset.theme = choice;
	}
</script>

<svelte:head>
	<title>Proof home</title>
</svelte:head>

<main class="controls">
	<h1>Proof home</h1>
	<a id="to-other" href="/other">To the other page</a>

	<button id="press" type="button" onclick={() => presses++}>Pressed {presses}</button>

	<form id="form" onsubmit={submit}>
		<label>Name <input id="name" name="name" type="text" bind:value={name} /></label>
		<label>
			Colour
			<select id="colour" name="colour" bind:value={colour}>
				<option value="teal">Teal</option>
				<option value="amber">Amber</option>
			</select>
		</label>
		<button id="submit" type="submit">Send</button>
		<output id="submitted">{submitted}</output>
	</form>

	<details id="menu" class="menu">
		<summary>Menu</summary>
		<a id="menu-other" href="/other">Other</a>
		<a id="menu-login" href="/login">Log in page</a>
	</details>

	<button id="open-dialog" type="button" onclick={() => dialog?.showModal()}>Open dialog</button>
	<dialog id="dialog" bind:this={dialog}>
		<p>Dialog body</p>
		<button id="close-dialog" type="button" onclick={() => dialog?.close()}>Close</button>
	</dialog>

	<fieldset id="theme">
		<legend>Theme</legend>
		{#each ['follow-system', 'light', 'dark'] as choice (choice)}
			<button
				type="button"
				data-theme-choice={choice}
				aria-pressed={theme === choice}
				onclick={() => chooseTheme(choice)}>{choice}</button
			>
		{/each}
	</fieldset>
</main>

<style>
	.controls {
		display: grid;
		gap: 12px;
		max-width: 480px;
		padding: 16px;
	}
	.menu a {
		display: block;
	}
</style>

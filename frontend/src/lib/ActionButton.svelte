<script lang="ts">
	import type { MouseEventHandler } from 'svelte/elements';

	let {
		label,
		busyWord,
		busy,
		disabled,
		variant,
		type = 'button',
		small = false,
		onclick,
		element = $bindable()
	}: {
		label: string;
		busyWord: string;
		busy: boolean;
		disabled: boolean;
		variant: 'primary' | 'secondary';
		type?: 'button' | 'submit';
		small?: boolean;
		onclick?: MouseEventHandler<HTMLButtonElement>;
		element?: HTMLButtonElement;
	} = $props();
</script>

<button
	bind:this={element}
	class={['btn', variant, small && 'sm']}
	{type}
	disabled={disabled || busy}
	aria-busy={busy ? 'true' : undefined}
	{onclick}
>
	{#if busy}<span class="state" data-state="building"><i></i></span>{busyWord}{:else}{label}{/if}
</button>

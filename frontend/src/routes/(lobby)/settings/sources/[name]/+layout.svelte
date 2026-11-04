<script lang="ts">
	import { onNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	// Every navigation onto a source page, to another name or to the same name in
	// another history entry, rebuilds the page's own state (a renewed secret, a
	// typed token, an open dialog and its counts) before the new data commits.
	// `onNavigate` fires for each committing navigation, Back and Forward
	// included; a `beforeNavigate` is skipped while another navigation is in flight.
	let visit = $state(0);
	onNavigate((navigation) => {
		if (navigation.to?.route.id === page.route.id) {
			visit += 1;
		}
	});
</script>

{#key visit}{@render children()}{/key}

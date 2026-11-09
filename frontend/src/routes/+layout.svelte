<script lang="ts">
	import 'overnightworks-design/design.css';
	import '../app.css';
	import { commitSession } from '#lib/session.svelte.ts';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	$effect.pre(() => {
		commitSession(data.session);
	});
</script>

{@render children()}

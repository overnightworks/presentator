<script lang="ts">
	import { LegalLinks } from 'overnightworks-appkit';
	import LobbyHeader from '#lib/LobbyHeader.svelte';
	import { legalLinks } from '#lib/legal.ts';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();
</script>

<LobbyHeader session={data.session} person={data.person} />
<main>{@render children()}</main>
<footer class="bottom">
	<LegalLinks links={legalLinks(data.session.words)} />
</footer>

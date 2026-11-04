<script lang="ts">
	import { LegalLinks } from 'overnightworks-appkit';
	import LobbyHeader from '#lib/LobbyHeader.svelte';
	import { legalLinks } from '#lib/legal.ts';
	import { Notice, provideNotice } from '#lib/notice.ts';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	// Lives as long as this layout, which is as long as the person stays in the lobby.
	provideNotice(new Notice());
</script>

<LobbyHeader session={data.session} person={data.person} />
<main>{@render children()}</main>
<footer class="bottom">
	<LegalLinks links={legalLinks(data.session.words)} />
</footer>

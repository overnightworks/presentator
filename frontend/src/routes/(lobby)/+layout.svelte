<script lang="ts">
	import LobbyHeader from '#lib/LobbyHeader.svelte';
	import { Notice, provideNotice } from '#lib/notice.ts';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	// Lives as long as this layout, which is as long as the person stays in the lobby.
	provideNotice(new Notice());
</script>

<LobbyHeader session={data.session} person={data.person} words={data.words} kitWords={data.kitWords} />
<main>{@render children()}</main>

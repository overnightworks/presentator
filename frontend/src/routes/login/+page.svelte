<script lang="ts">
	import { SignInPage } from 'overnightworks-appkit';
	import { legalLinks } from '#lib/legal.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);
</script>

<SignInPage
	appName={words.wordmark}
	state={{ page: 'signed-out', signInHref: data.session.sign_in.start }}
	legalLinks={legalLinks(words)}
/>

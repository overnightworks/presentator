<script lang="ts">
	import { SignInPage } from 'overnightworks-appkit';
	import { legalLinks } from '#lib/legal.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);
	const addresses = $derived(data.session.sign_in);
</script>

<SignInPage
	appName={words.wordmark}
	state={{ page: 'login-failed', signInHref: addresses.start }}
	legalLinks={legalLinks(words)}
/>

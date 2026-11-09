<script lang="ts">
	import { SignInPage } from 'overnightworks-design';
	import { signInPageWords } from '#lib/kitWords.ts';
	import { legalLinks } from '#lib/legal.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.session.words);
	const addresses = $derived(data.session.sign_in);
</script>

<SignInPage
	appName={words.wordmark}
	state={{ page: 'no-access', logOutAction: addresses.logout }}
	legalLinks={legalLinks(words)}
	words={signInPageWords(words)}
/>

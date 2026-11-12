<script lang="ts">
	import { SignInPage } from 'overnightworks-design';
	import { signInPageWords } from '#lib/kitWords.ts';
	import { legalLinks } from '#lib/legal.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.words);
</script>

<SignInPage
	appName={words['app.name']}
	state={{ page: 'signed-out', signInHref: data.session.sign_in.start }}
	legalLinks={legalLinks(words)}
	words={signInPageWords(words)}
/>

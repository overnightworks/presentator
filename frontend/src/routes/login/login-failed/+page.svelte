<script lang="ts">
	import { SignInPage } from 'overnightworks-design';
	import { legalLinks } from '#lib/legal.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const words = $derived(data.words);
	const addresses = $derived(data.session.sign_in);
</script>

<SignInPage
	appName={words['app.name']}
	state={{ page: 'login-failed', signInHref: addresses.start }}
	legalLinks={legalLinks(words)}
	words={data.kitWords}
/>

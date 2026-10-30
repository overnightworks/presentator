<script>
	import { SignInPage } from 'overnightworks-appkit';
</script>

<SignInPage
	appName="presentator"
	state={{ page: 'signed-out', signInHref: '/login/oidc' }}
	legalLinks={[
		{ label: 'Impressum', href: '/legal/impressum' },
		{ label: 'Datenschutz', href: '/legal/datenschutz' }
	]}
/>

<script lang="ts">
	import { AccountMenu } from 'overnightworks-design';
	import { accountMenuWords } from '#lib/kitWords.ts';
	import { type ThemeChoice, writeTheme } from '#lib/api/account.ts';
	import type { SessionPerson, SessionView } from '#lib/api/session.ts';
	import { legalLinks } from '#lib/legal.ts';
	import { currentTheme, showTheme } from '#lib/session.svelte.ts';
	import type { Words } from '#lib/words/de.ts';

	let {
		session,
		person,
		words
	}: { session: SessionView; person: SessionPerson; words: Words } = $props();
	let popover: HTMLElement;

	// The radio shows the choice at once and snaps back when the write fails.
	async function chooseTheme(choice: ThemeChoice): Promise<void> {
		const before = currentTheme();
		showTheme(choice);
		try {
			await writeTheme(fetch, choice);
		} catch (failure) {
			showTheme(before);
			throw failure;
		}
	}
</script>

<button
	class="account"
	type="button"
	popovertarget="account-menu"
	aria-label={words['account.title']}
>
	<span aria-hidden="true" class="as-typed">{person.username.slice(0, 1).toUpperCase()}</span>
</button>
<div id="account-menu" class="account-menu" popover="auto" bind:this={popover}>
	<AccountMenu
		name={person.username}
		accountHref={session.sign_in.account_console_url}
		onAccountOpen={() => popover.hidePopover()}
		appRows={[]}
		theme={{ choice: currentTheme(), onChange: chooseTheme }}
		legalLinks={legalLinks(words)}
		words={accountMenuWords(words)}
		logOutAction={session.sign_in.logout}
	/>
</div>

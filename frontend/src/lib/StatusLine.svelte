<script lang="ts">
	let { shape, text }: { shape: 'ready' | 'never-built' | 'failed'; text: string } = $props();
</script>

<span class="state line" data-state={shape} role="status"><i></i>{text}</span>

<style>
	.line {
		align-items: flex-start;
		white-space: normal;
	}
	.line i {
		margin-top: 2px;
	}
</style>

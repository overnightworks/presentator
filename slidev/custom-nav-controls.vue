<script setup>
import { useNav } from '@slidev/client'

import CoPresenter from './components/CoPresenter.vue'

const { isPresenter } = useNav()
</script>

<template>
  <!-- Slidev's presenter page has no slot above its sections, so the bar is
       teleported into the page's first child, under the progress and timer bars. -->
  <Teleport
    v-if="isPresenter"
    defer
    to=".slidev-presenter > :first-child"
  >
    <div class="presenter-bar">
      <a
        class="slidev-icon-btn"
        href="/"
        aria-label="Home"
        title="Home"
      >
        <div
          class="i-carbon:home"
          aria-hidden="true"
        />
      </a>
      <CoPresenter />
    </div>
  </Teleport>
</template>

<style scoped>
.presenter-bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 8px;
  border-bottom: 1px solid color-mix(in srgb, currentcolor 20%, transparent);
}
.presenter-bar .slidev-icon-btn:focus-visible {
  outline: 2px solid CanvasText;
  outline-offset: 2px;
}
</style>

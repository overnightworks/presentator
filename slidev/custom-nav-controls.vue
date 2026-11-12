<script setup>
import { useNav } from '@slidev/client'

import CoPresenter from './components/CoPresenter.vue'
import { controlWords } from './controls-words.js'

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
        v-if="controlWords"
        class="slidev-icon-btn"
        href="/"
        :aria-label="controlWords['controls.home']"
        :title="controlWords['controls.home']"
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

<script setup>
import { useDarkMode } from '@slidev/client'

import { Phase, useRoomNotice } from './room-notice.js'

const { isDark } = useDarkMode()
const notice = useRoomNotice()
</script>

<template>
  <div
    v-if="notice"
    class="room-notice"
    :class="{ dark: isDark }"
  >
    <p
      v-if="notice.phase === Phase.BANNER"
      class="banner"
      :data-room-notice="notice.phase"
      v-text="notice.text"
    />
    <p
      v-else
      class="pill"
      :data-room-notice="notice.phase"
    >
      <svg
        v-if="notice.phase === Phase.ANSWERING"
        class="glyph"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path d="M11 5 6 9H2v6h4l5 4V5Z" />
        <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
        <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
      </svg>
      <span
        v-else
        class="dot"
      />
      <span v-text="notice.text" />
    </p>
  </div>
</template>

<style scoped>
.room-notice {
  --notice-background: #0b1f33f0;
  --notice-color: #fff;
  --notice-dot: #56c3cd;
  position: absolute;
  inset: 0;
  z-index: 100;
  pointer-events: none;
  user-select: none;
  font-family: ui-sans-serif, system-ui, sans-serif;
}
.room-notice.dark {
  --notice-background: #eef3f8f2;
  --notice-color: #0b1f33;
  --notice-dot: #0c6c77;
}
.banner,
.pill {
  position: absolute;
  margin: 0;
  background: var(--notice-background);
  color: var(--notice-color);
  letter-spacing: 0;
}
.banner {
  inset: auto 0 0;
  padding: 1em 1.6em;
  font-size: 28px;
  line-height: 1.4;
  text-align: left;
}
.pill {
  top: 3%;
  right: 2.5%;
  display: inline-flex;
  align-items: center;
  gap: 0.55em;
  padding: 0.4em 0.95em;
  border-radius: 999px;
  font-size: 19px;
  font-weight: 700;
  line-height: 1.3;
  white-space: nowrap;
}
.dot {
  width: 0.75em;
  height: 0.75em;
  flex: none;
  border-radius: 50%;
  background: var(--notice-dot);
  box-shadow: 0 0 0 0.3em color-mix(in srgb, var(--notice-dot) 35%, transparent);
  animation: pulse 1.6s ease-in-out infinite;
}
.glyph {
  width: 1.25em;
  height: 1.25em;
  flex: none;
  fill: none;
  stroke: currentcolor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}
@keyframes pulse {
  50% { box-shadow: 0 0 0 0.1em color-mix(in srgb, var(--notice-dot) 20%, transparent); }
}
@media (prefers-reduced-motion: reduce) {
  .dot { animation: none; }
}
</style>

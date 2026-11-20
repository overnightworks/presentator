<script setup>
import { useNav } from '@slidev/client'
import { watch } from 'vue'

/* global window */

// The spike's bridge: reports slide and click to the framing page and accepts
// a go-to back, dropping anything that is not an integer inside the deck.
const { currentPage, clicks, clicksTotal, total, go } = useNav()

const isIntegerWithin = (value, minimum, maximum) =>
  Number.isInteger(value) && value >= minimum && value <= maximum

const report = () => {
  window.parent.postMessage(
    {
      type: 'state',
      slide: currentPage.value,
      click: clicks.value,
      clicksTotal: clicksTotal.value,
      total: total.value,
    },
    '*',
  )
}

watch([currentPage, clicks, clicksTotal], report, { immediate: true })

window.addEventListener('message', (event) => {
  if (event.source !== window.parent) return
  const message = event.data
  if (message?.type !== 'go') return
  if (!isIntegerWithin(message.slide, 1, total.value)) return
  const clickLimit = message.slide === currentPage.value ? clicksTotal.value : 0
  if (!isIntegerWithin(message.click, 0, clickLimit)) return
  go(message.slide, message.click)
})
</script>

<template>
  <span />
</template>

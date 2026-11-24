<script setup lang="ts">
import { computed } from 'vue'
import { countryFlagShapes } from '@cyshuhe/shared'

const props = withDefaults(
  defineProps<{
    code?: string | null
    size?: 'sm' | 'md'
  }>(),
  {
    code: '',
    size: 'md',
  },
)

const shapes = computed(() => countryFlagShapes(props.code))
</script>

<template>
  <svg
    v-if="shapes.length"
    class="cy-country-flag"
    :class="`cy-country-flag--${size}`"
    viewBox="0 0 24 16"
    aria-hidden="true"
    focusable="false"
  >
    <component :is="s.tag" v-for="(s, idx) in shapes" :key="idx" v-bind="s.attrs" />
  </svg>
</template>

<style scoped>
.cy-country-flag {
  display: inline-block;
  width: 21px;
  height: 14px;
  border: 1px solid #d5dee8;
  border-radius: 2px;
  flex-shrink: 0;
  vertical-align: -2px;
  box-sizing: border-box;
  background: #ffffff;
}
.cy-country-flag--sm {
  width: 18px;
  height: 12px;
  vertical-align: -1px;
}
</style>
